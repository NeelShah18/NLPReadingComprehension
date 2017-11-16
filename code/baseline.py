# -*- coding: utf-8 -*-

from __future__ import absolute_import
from __future__ import division
from __future__ import print_function

import os
import json
import random
import time
import logging

import numpy as np
from six.moves import xrange
from os.path import join as pjoin
import tensorflow as tf

from evaluate import exact_match_score, f1_score, evaluate
import sys
import math
import re

logging.basicConfig(level=logging.INFO)

# Where we specify directories and such
tf.app.flags.DEFINE_string("data_dir", "data/squad", "SQuAD directory (default ./data/squad)")
tf.app.flags.DEFINE_string("train_dir", "tttttksyoootrains", "Training directory to save the model parameters (default: ./train).")
tf.app.flags.DEFINE_string("load_train_dir", "tttttksyoootrains", "Training directory to load model parameters from to resume training (default: {train_dir}).")
tf.app.flags.DEFINE_string("log_dir", "log", "Path to store log and flag files (default: ./log)")
tf.app.flags.DEFINE_string("vocab_path", "data/squad/vocab.dat", "Path to vocab file (default: ./data/squad/vocab.dat)")
tf.app.flags.DEFINE_string("embed_path", "./data/squad/glove.trimmed.100.npz", "Path to the trimmed GLoVe embedding (default: ./data/squad/glove.trimmed.{embedding_size}.npz)")
tf.app.flags.DEFINE_string("saved_name", "model", "base name for our model")

# Where we specify data hyperparameters
tf.app.flags.DEFINE_integer("max_passage_length", 324, "Length of each passage that we require")
tf.app.flags.DEFINE_integer("max_question_length", 23, "Length of each question that we require")
tf.app.flags.DEFINE_integer("embedding_size", 100, "Size of the pretrained vocabulary.")
tf.app.flags.DEFINE_integer("num_of_val_entries", 204, "Number of entries we want in the val dataset.")
tf.app.flags.DEFINE_integer("num_of_test_entries", 204, "Number of entries we want in the test dataset.")
tf.app.flags.DEFINE_integer("num_of_train_entries", 3877, "Number of entries we want in the train dataset.")

# Where we specify training hyper parameters
tf.app.flags.DEFINE_float("start_learning_rate", 0.01, "Learning rate we start with before decaying.")
tf.app.flags.DEFINE_float("learning_decay_rate", 0.96, "Rate at which learning rate decays exponentially.")
tf.app.flags.DEFINE_integer("num_decay_steps", 1000, "decayed_learning_rate = learning_rate*decay_rate ^ (global_step / num_decay_steps)")
tf.app.flags.DEFINE_float("max_gradient_norm", 10.0, "Clip gradients to this norm.")
tf.app.flags.DEFINE_integer("batch_size", 100, "Batch size to use during training.")
tf.app.flags.DEFINE_integer("epochs", 10, "Number of epochs to train.")

tf.app.flags.DEFINE_boolean('should_use_new_loss', False, 'If should use our new loss function.')
tf.app.flags.DEFINE_boolean('should_use_dp_prediction', False, 'If should use our DP for answer prediction.')


# Temp hack for baseline
tf.app.flags.DEFINE_integer("start_epoch", 0, "Epoch to start with.")
tf.app.flags.DEFINE_string("optimizer", "adam", "adam / sgd")
tf.app.flags.DEFINE_integer("size_train_dataset", 20, "The size of the training dataset")

# Where we specify model architecture hyperparameters
tf.app.flags.DEFINE_float("dropout", .85, "Fraction of units randomly kept on non-recurrent connections.")
tf.app.flags.DEFINE_integer("state_size", 200, "Size of each layer in the Encoder.")
tf.app.flags.DEFINE_integer("output_size", 750, "The output size of your model.")
tf.app.flags.DEFINE_integer("eval_num_samples", 100, "How many samples to evaluate.")
tf.app.flags.DEFINE_integer("val_num_batches", 50, "Per how many batches do we run on a validation sample and save the model.")
tf.app.flags.DEFINE_integer("num_keep_checkpoints", 5, "How many checkpoints to keep, 0 indicates keep all.")
tf.app.flags.DEFINE_float("val_cost_frac", 0.05, "Fraction of validation set used for periodic evaluation.")
tf.app.flags.DEFINE_float("sigma_threshold", 0.5, "Threshold to apply to answer probabilities in order to determine answer indices")
tf.app.flags.DEFINE_float("l2_lambda", 0.01, "Amount of L2 regularization we want to apply to our parameters.")

# Where we specify model architecture add-ons
tf.app.flags.DEFINE_boolean("quadratic_form", False, "Whether to convert coattention to a quadratic form by adding a new weight matrix.")

FLAGS = tf.app.flags.FLAGS

def load_token_file(file_name):
    data = []
    file_contents = open(file_name, "rb")
    for line in file_contents:
        # Assumes already in the space delimited token index format
        data.append(line.rstrip()) # Get rid of trailing newline
    return data

def load_span_file(file_name):
    data = []
    file_contents = open(file_name, "rb")
    for line in file_contents:
        # Assumes space delimited
        left_point, right_point = line.rstrip().split(" ")
        # Convert to ints and add to list
        data.append((int(left_point), int(right_point)))
    return data
    return True

def load_datasets():
    print ('Entered load_datasets')
    # Do what you need to load datasets from FLAGS.data_dir
    # We load the .ids. file because in qa_answer they are also loaded
    dataset_dir = FLAGS.data_dir
    abs_dataset_dir = os.path.abspath(dataset_dir)
    # We will no longer use the train dataset, we are only going to be using the val dataset and splitting it up into our own test, train, and val datasets
    # NOTE: get all the files that we want to load
    answer_file = os.path.join(abs_dataset_dir, "val.answer")
    context_file = os.path.join(abs_dataset_dir, "val.context")
    question_file = os.path.join(abs_dataset_dir, "val.question")
    ids_context_file = os.path.join(abs_dataset_dir, "val.ids.context")
    ids_question_file = os.path.join(abs_dataset_dir, "val.ids.question")
    span_answer_file = os.path.join(abs_dataset_dir, "val.span")

    # NOTE: Get data by loading in the files we just made using load_token_file
    # For context and question, we assume each item in the list is a string
    # The string is space seperated list of tokens that correspond to indices in the vocabulary
    # We assume this since this is what's passed in for qa_answer.py
    # Since this isn't in qa_answer.py, we assume each item in the list to be a tuple
    # The first place in the tuple is the starting index relative to the passage
    # NOTE: it's possible for both values to be the same
    valid_context_data = load_token_file(context_file)
    valid_question_data = load_token_file(question_file)
    valid_answer_data = load_token_file(answer_file)

    if (len(valid_context_data) != len(valid_question_data) or len(valid_context_data) != len(valid_answer_data)):
        print('Error: the number of paragraphs, questions, and answers do not match')
          
    # Make an array of indices 0 ... (len(valid_context_data) - 1)
    indices_available = []
    for index in range(0, len(valid_context_data)):
        indices_available.append(index)
    
    new_val_context_data = []
    new_val_question_data = []
    new_val_answer_data = []

    new_test_context_data = []
    new_test_question_data = []
    new_test_answer_data = []
    
    new_train_context_data = []
    new_train_question_data = []
    new_train_answer_data = []
    
    # Note: set up the new_val datasets for context, question, answer
    for i in range(FLAGS.num_of_val_entries):
        rand_index = indices_available[random.randrange(0,len(indices_available))]
        new_val_context_data.append(valid_context_data[rand_index])
        new_val_question_data.append(valid_question_data[rand_index])
        new_val_answer_data.append(valid_answer_data[rand_index])
        indices_available.remove(rand_index)
    for i in range(FLAGS.num_of_test_entries):
        rand_index = indices_available[random.randrange(0,len(indices_available))]
        new_test_context_data.append(valid_context_data[rand_index])
        new_test_question_data.append(valid_question_data[rand_index])
        new_test_answer_data.append(valid_answer_data[rand_index])
        indices_available.remove(rand_index)
    for index in indices_available:
        new_train_context_data.append(valid_context_data[index])
        new_train_question_data.append(valid_question_data[index])
        new_train_answer_data.append(valid_answer_data[index])

    # Merge data
    new_val_dataset = (new_val_context_data, new_val_question_data, new_val_answer_data)
    new_test_dataset = (new_test_context_data, new_test_question_data, new_test_answer_data)
    new_train_dataset = (new_train_context_data, new_train_question_data, new_train_answer_data)
    return (new_val_dataset, new_test_dataset, new_train_dataset)

def getNumWordsCommonInPhrases(phrase1, phrase2):
	# We want to return the number of words in phrase that are also in question (phrase1)
	num_common_words = 0
	words = phrase2.split(" ")
	for word in words:
		if word in phrase1:
			num_common_words = num_common_words + 1
	return num_common_words

# probability returned is the number of words that are commmon / number of words in correct_answer
# "Who ate a sandwich" predicted_answer = Bobby eats a sandwich" correct_answer = "Bobby" 1/1 1/4
def evalFnOverNumWordsInCorrectAnswer(predicted_answer, correct_answer):
	num_common_words = getNumWordsCommonInPhrases(predicted_answer, correct_answer)
	words_in_correct_answer = correct_answer.split(" ")
	return (num_common_words * 1.0) / len(words_in_correct_answer)

def evalFnOverNumWordsInPredictedAnswer(predicted_answer, correct_answer):
	num_common_words = getNumWordsCommonInPhrases(predicted_answer, correct_answer)
	words_in_predicted_answer = predicted_answer.split(" ")
	return (num_common_words * 1.0) / len(words_in_predicted_answer)

def evalFnAverage(predicted_answer, correct_answer):
	sum_eval_metrics = evalFnOverNumWordsInCorrectAnswer(predicted_answer, correct_answer) + evalFnOverNumWordsInPredictedAnswer(predicted_answer, correct_answer)
	return sum_eval_metrics / 2.0

#Baseline: To get the context with question and answer
def baseline(train_dataset):
	train_context = train_dataset[0]
	train_question = train_dataset[1]
	train_answer = train_dataset[2]

	list_of_evaluation_metrics_over_correct_answer = []
	list_of_evaluation_metrics_over_predicted_answer = []
	list_of_evaluation_metrics_avg = []
	#Iterate through the context and questions
	for i in xrange(len(train_question)):
		passage = train_context[i]
		question = train_question[i]
		correct_answer = train_answer[i]
		#phrase with maximum count
		max_phrase = ""
		#Max count of matching words
		max_count = 0
		#The phrases within the paragraph
		print (passage,  "\n")
		phrases = re.split("\.|;|,|\(|\)", passage)
		print(phrases)
		for phrase in phrases:
			num_words_common_in_question = getNumWordsCommonInPhrases(question, phrase)
			if num_words_common_in_question > max_count:
				max_count = num_words_common_in_question
				max_phrase = phrase
		#Max phrase is now the predicted answer in baseline

		evaluation_metric_over_correct_answer = evalFnOverNumWordsInCorrectAnswer(max_phrase, correct_answer)
		list_of_evaluation_metrics_over_correct_answer.append(evaluation_metric_over_correct_answer)

		evaluation_metric_over_predicted_answer = evalFnOverNumWordsInPredictedAnswer(max_phrase, correct_answer)
		list_of_evaluation_metrics_over_predicted_answer.append(evaluation_metric_over_predicted_answer)

		evaluation_metric_avg = evalFnAverage(max_phrase, correct_answer)
		list_of_evaluation_metrics_avg.append(evaluation_metric_avg)

	avg_evaluation_metric_over_correct_answer = sum(list_of_evaluation_metrics_over_correct_answer) / (len(list_of_evaluation_metrics_over_correct_answer) * 1.0)
	print ('avg_evaluation_metric_over_correct_answer = ', avg_evaluation_metric_over_correct_answer)

	avg_evaluation_metric_over_predicted_answer = sum(list_of_evaluation_metrics_over_predicted_answer) / (len(list_of_evaluation_metrics_over_predicted_answer) * 1.0)
	print ('avg_evaluation_metric_over_predicted_answer = ', avg_evaluation_metric_over_predicted_answer)

	avg_evaluation_metric_avg = sum(list_of_evaluation_metrics_avg) / (len(list_of_evaluation_metrics_avg) * 1.0)
	print ('avg_evaluation_metric_avg = ', avg_evaluation_metric_avg)

def main(_):
	val_dataset, test_dataset, train_dataset = load_datasets()
	baseline(train_dataset)

if __name__ == "__main__":
    tf.app.run()


