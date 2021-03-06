from data_handling import db_handling
from data_handling import data_prep
from model import SentimentModel
from TreeLSTM import data_utils

import numpy as np
import sklearn
import pickle
import os
import datetime
import logging
import time

SEED = 22
NUM_LABELS = 3

EMB_DIM = 300
HIDDEN_DIM = 100
TRAIN_SPLIT_PERCENTAGE = 0.85

LEARNING_RATE = 0.01
DEPENDENCY = False

NUM_EPOCHS = 30
ADA_DELTA = True

GLOVE_DIR = '../Data/Glove/'
PARAMS_PICKLE_FILE_PATH = db_handling.sentiment104_PATH + 'params.pickle'
SENT140_PATH = '../Data/sentiment140/'
TWEETS_COLLECTED_DIR = '../Data/tweets_collected/'
SST_DIR_PATH = '../Data/sst/'
VOCAB_FILE = '../Data/vocab_merged.txt'
SANDERS_SST_FUSION_PATH = '../Data/sanders_sst_fusion/'


def get_model(num_emb, output_dim, max_degree):
    return SentimentModel(
        num_emb, EMB_DIM, HIDDEN_DIM, output_dim,
        degree=max_degree, learning_rate=LEARNING_RATE,
        trainable_embeddings=True,
        labels_on_nonroot_nodes=False,
        irregular_tree=DEPENDENCY, ada_delta=ADA_DELTA)

def train(vocab, data, param_initialization = None, param_load_file_path = None, param_dump_file_path = None,
          data_batched = False, metrics_dump_path = None, num_epochs=NUM_EPOCHS):
    #set seed
    np.random.seed(SEED)

    logging.info('\n' + " --- STARTED TRAINING SESSION: " + str(datetime.datetime.now()) + ' ---')

    assert type(data) is dict

    num_emb = vocab.size()
    num_labels = NUM_LABELS
    max_degree = 2

    if data_batched:
        #load and concatenate dev data
        dev_set = data_prep.load_and_concatenate_dumps(data['dev'])
        train_count = 0
        # assert that data is labeled the right way
        assert set([label for _, label in dev_set]) <= set([0, 1, 2])
        for batch_nr, train_dump_file in enumerate(data['train']):
            train_batch = pickle.load(open(train_dump_file, 'rb'))
            train_count += len(train_batch)
            assert set([label for _, label in train_batch]) <= set([0, 1, 2])

            labels = [label for _, label in train_batch]
            print(labels.count(0), labels.count(1), labels.count(2))

            logging.info('Batch ' + str(batch_nr + 1) + ' of ' + str(len(data['train'])) + ' OK')
        logging.info('train ' + str(train_count))
    else:
        train_set, dev_set = data['train'], data['dev']
        # assert that data is labeled the right way
        for key, dataset in data.items():
            labels = [label for _, label in dataset]
            assert set(labels) <= set([0, 1, 2])
            logging.info('train ' + str(len(train_set)))

    logging.info('dev ' + str(len(dev_set)))
    logging.info('num emb: ' + str(num_emb))
    logging.info('num labels: ' + str(num_labels))

    model = get_model(num_emb, num_labels, max_degree)
    logging.info('Initialized new model')

    if param_initialization:
        model.set_params(param_initialization)
        logging.info('set params')
    elif param_load_file_path:
        model.set_params(pickle_file_path=param_load_file_path)
        logging.info('loaded param initialization from: ' + param_load_file_path)
    else:
        # initialize model embeddings with GloVe
        model.initialize_model_embeddings(vocab, GLOVE_DIR)

    metrics_dict = {'avg_loss': [], 'dev_accuracy': [], 'f1_score': [], 'conf_matrix': []}

    ts = time.clock()
    batches_left = num_epochs * len(data['train'])
    #perform training and evaluation steps
    for epoch in range(num_epochs):
        if data_batched:
            for batch_nr, train_dump_file in enumerate(data['train']):
                logging.info('EPOCH ' + str(epoch) +'| Batch ' + str(batch_nr + 1) + ' of ' + str(len(data['train'])))

                train_batch = pickle.load(open(train_dump_file, 'rb'))
                avg_loss = train_dataset(model, train_batch)
                logging.info('avg loss ' + str(avg_loss))

                if param_dump_file_path:
                    model.get_params(pickle_file_path=param_dump_file_path)
                    logging.info('Dumped model parameters to: ' + param_dump_file_path)

                dev_accuracy, f1_score, conf_matrix = evaluate_dataset(model, dev_set)
                logging.info('dev accuracy ' + str(dev_accuracy) + ' f1 score ' + str(f1_score))

                batches_left += -1
                logging.info('----- Estimated time till finish: ' + str((time.clock() - ts)*batches_left) + ' sec')
                ts = time.clock()

        #persist metrics data
        metrics_dict = add_to_metrics_dict(metrics_dict, avg_loss, dev_accuracy, f1_score, conf_matrix)
        if metrics_dump_path:
            pickle.dump(metrics_dict, open(metrics_dump_path, 'wb'))

        else:
            logging.info('EPOCH ' + str(epoch))
            avg_loss = train_dataset(model, train_set)
            logging.info('avg loss ' + str(avg_loss))

            dev_accuracy, f1_score, conf_matrix = evaluate_dataset(model, dev_set)
            metrics_dict = add_to_metrics_dict(metrics_dict, avg_loss, dev_accuracy, f1_score, conf_matrix)
            logging.info('dev accuracy ' + str(dev_accuracy) + ' f1 score ' + str(f1_score))
            if metrics_dump_path:
                pickle.dump(metrics_dict, open(metrics_dump_path, 'wb'))

            if param_dump_file_path:
                model.get_params(pickle_file_path=param_dump_file_path)
                logging.info('Dumped model parameters to: ' + param_dump_file_path)

    return model.get_params(param_dump_file_path), metrics_dict

def train_dataset(model, data):
    losses = []
    avg_loss = 0.0
    total_data = len(data)
    for i, (tree, _) in enumerate(data):
        loss, pred_y = model.train_step(tree, None)  # labels will be determined by model
        losses.append(loss)
        avg_loss = avg_loss * (len(losses) - 1) / len(losses) + loss / len(losses)
        print('avg loss %.2f at example %d of %d\r' % (avg_loss, i, total_data))
    return np.mean(losses)

def evaluate_dataset(model, data):
    #calculates accuracy and f1 metric
    num_correct = 0
    i = 0
    label_array = []
    pred_array = []
    conf_matrix = np.zeros([model.output_dim, model.output_dim])

    for tree, label in data:
        pred_y = model.predict(tree)[-1]  # root pred is final row
        predicted_label = np.argmax(pred_y)
        num_correct += (label == predicted_label)
        conf_matrix[label, predicted_label] += 1
        label_array.append(label)
        pred_array.append(predicted_label)
        i += 1

    accuracy = float(num_correct) / len(data)
    if len(set(label_array)) > 2:
        f1_score = sklearn.metrics.f1_score(label_array, pred_array, average='weighted')
    else:
        f1_score = sklearn.metrics.f1_score(label_array, pred_array, pos_label=2, average='binary')
    return accuracy, f1_score, conf_matrix

def train_on_sent140(vocab_file_path = VOCAB_FILE, param_initialization = None,
                     dump_dir = '../Data/sentiment140/dump_test/'): #TODO change back to dump/train dir
    vocab = load_vocab(vocab_file_path)

    #pass data as dict with dump file paths (indicated by data_batched=True)
    data = {}
    train_dir = dump_dir + 'train/'
    dev_dir = dump_dir + 'dev/'
    data['train'] = [train_dir + file for file in os.listdir(train_dir)]
    data['dev'] = [dev_dir + file for file in os.listdir(dev_dir)]
    return train(vocab, data, data_batched=True, metrics_dump_path=dump_dir + 'metrics.pickle',
                 param_initialization=param_initialization, param_dump_file_path=dump_dir + 'params.pickle')

def train_on_tweets_collected(vocab_file_path = VOCAB_FILE, num_epochs=NUM_EPOCHS, data_dir='../Data/tweets_collected/dump/',
                              dump_dir='../Data/tweets_collected/dump/', param_load_file_path=None):
    vocab = load_vocab(vocab_file_path)

    # pass data as dict with dump file paths (indicated by data_batched=True)
    data = {}
    train_dir = data_dir + 'train/'
    dev_dir = data_dir + 'dev/'
    data['train'] = [train_dir + file for file in os.listdir(train_dir)]
    data['dev'] = [dev_dir + file for file in os.listdir(dev_dir)]
    return train(vocab, data, data_batched=True, metrics_dump_path=dump_dir + 'metrics.pickle', num_epochs=num_epochs,
                 param_load_file_path=param_load_file_path, param_dump_file_path=dump_dir + 'params.pickle')

def train_on_sst(vocab_file_path = VOCAB_FILE, param_initialization = None, num_epochs=NUM_EPOCHS,
                              dump_dir='../Data/sst/dump/'):
    vocab = load_vocab(vocab_file_path)

    _, data = pickle.load(open(SST_DIR_PATH + 'sst_data.pickle', 'rb'))
    del data['max_degree']
    return train(vocab, data, metrics_dump_path=dump_dir + 'metrics.pickle', num_epochs=num_epochs,
                 param_initialization=param_initialization, param_dump_file_path=dump_dir + 'params.pickle')

def load_vocab(vocab_file_path):
    vocab = data_utils.Vocab()
    vocab.load(vocab_file_path)
    return vocab

def add_to_metrics_dict(metrics_dict, avg_loss, dev_accuracy, f1_score, conf_matrix):
    metrics_dict['avg_loss'].append(avg_loss)
    metrics_dict['dev_accuracy'].append(dev_accuracy)
    metrics_dict['f1_score'].append(f1_score)
    metrics_dict['conf_matrix'].append(conf_matrix)
    return metrics_dict

def train_on_sst_and_sanders(num_epochs=30, param_initialization = None):
    vocab = pickle.load(open(SANDERS_SST_FUSION_PATH + 'vocab.pickle', 'rb'))
    dump_dir = SANDERS_SST_FUSION_PATH + 'dump/'
    data = {}
    data['train'] = pickle.load(open(SANDERS_SST_FUSION_PATH + 'train.pickle', 'rb'))
    data['dev'] = pickle.load(open(SANDERS_SST_FUSION_PATH + 'dev.pickle', 'rb'))
    return train(vocab, data, metrics_dump_path=dump_dir + 'metrics.pickle', num_epochs=num_epochs,
                 param_initialization=param_initialization, param_dump_file_path=dump_dir + 'params.pickle')

def evaluate_model(vocab_file_path, param_load_file_path, validation_data_path):
    if 'pickle' in vocab_file_path:
        vocab = pickle.load(open(vocab_file_path, 'rb'))
    else:
        vocab = load_vocab(vocab_file_path)
    num_emb = vocab.size()
    print(num_emb)

    model = get_model(num_emb, NUM_LABELS, max_degree=2)
    model.set_params(pickle_file_path=param_load_file_path)

    validation_data = pickle.load(open(validation_data_path, 'rb'))

    if len(validation_data[0]) == 2:
        accuracy, f1_score, conf_matrix = evaluate_dataset(model, validation_data)
    elif len(validation_data[0]) == 3: #includes lags
        accuracy, f1_score, conf_matrix, profit = eval_data_w_lags(model, validation_data)
        print('profit:', profit)

    print('Accuracy: ', accuracy)
    print('F1 Score', f1_score)
    print('Confusion Matrix')
    print(conf_matrix)

def eval_data_w_lags(model, data, print_results=True):
    # calculates accuracy and f1 metric
    num_correct = 0
    i = 0
    label_array = []
    pred_array = []
    profit_array = []
    conf_matrix = np.zeros([model.output_dim, model.output_dim])


    for tree, label, lag in data:
        pred_y = model.predict(tree)[-1]  # root pred is final row
        predicted_label = np.argmax(pred_y)
        profit_array.append((predicted_label-1)*lag)
        num_correct += (label == predicted_label)
        conf_matrix[label, predicted_label] += 1
        label_array.append(label)
        pred_array.append(predicted_label)
        i += 1

    accuracy = float(num_correct) / len(data)
    mean_profit = np.mean(profit_array)
    if len(set(label_array)) > 2:
        f1_score = sklearn.metrics.f1_score(label_array, pred_array, average='weighted')
    else:
        f1_score = sklearn.metrics.f1_score(label_array, pred_array, pos_label=2, average='binary')

    if print_results:
        print('Accucacy:', accuracy)
        print('F1 Score:', f1_score)
        print('Conf Matrix', conf_matrix)
        print('Profit', mean_profit)
    return accuracy, f1_score, conf_matrix, mean_profit

def load_model(vocab_file_path, param_load_file_path, num_labels=3):
    if 'pickle' in vocab_file_path:
        vocab = pickle.load(open(vocab_file_path, 'rb'))
    else:
        vocab = load_vocab(vocab_file_path)
    num_emb = vocab.size()

    model = get_model(num_emb, num_labels, max_degree=2)
    model.set_params(pickle_file_path=param_load_file_path)
    return model
