#!/usr/bin/env python
# -*- coding: utf-8 -*-
################################################################################
#
# Copyright (c) 2019 Baidu.com, Inc. All Rights Reserved
#
################################################################################
"""
File: conversation_strategy.py
"""

import sys

sys.path.append("../")
import network
from tools.convert_conversation_corpus_to_model_text import preprocessing_for_one_conversation

def load():
    """
    load model
    """
    return network.main()


def predict(generator, text):
    """
    predict
    """
    model_text, topic_dict = \
        preprocessing_for_one_conversation(text.strip(), \
                                           topic_generalization=True)

    src, tgt, cue = model_text.split('\t')
    cue = cue.split('\1')

    response = generator.interact(src, cue)

    topic_list = sorted(topic_dict.items(), key=lambda item: len(item[1]), reverse=True)
    for key, value in topic_list:
        response = response.replace(key, value)

    return response


def main():
    """
    main
    """
    generator = load()
    for line in sys.stdin:
        response = predict(generator, line.strip())
        print(response)


if __name__ == '__main__':
    try:
        main()
    except KeyboardInterrupt:
        print("\nExited from the program ealier!")
