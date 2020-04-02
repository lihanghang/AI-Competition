#!/usr/bin/env python
# -*- coding: utf-8 -*-
################################################################################
#
# Copyright (c) 2019 Baidu.com, Inc. All Rights Reserved
#
################################################################################
"""
File: source/decoders/hgfu_rnn_decoder.py
"""

import torch
import torch.nn as nn

from KG_modules.attention import Attention
from KG_modules.decoders.state import DecoderState
from utils.misc import Pack
from utils.misc import sequence_mask


class RNNDecoder(nn.Module):
    """
    A HGFU GRU recurrent neural network decoder.
    Paper <<Towards Implicit Content-Introducing for Generative Short-Text
            Conversation Systems>>
    """
    def __init__(self,
                 input_size,
                 hidden_size,
                 output_size,
                 embedder=None,
                 num_layers=1,
                 attn_mode=None,
                 attn_hidden_size=None,
                 memory_size=None,
                 feature_size=None,
                 dropout=0.0,
                 concat=False):
        super(RNNDecoder, self).__init__()

        self.input_size = input_size
        self.hidden_size = hidden_size
        self.output_size = output_size
        self.embedder = embedder
        self.num_layers = num_layers
        self.attn_mode = None if attn_mode == 'none' else attn_mode
        self.attn_hidden_size = attn_hidden_size or hidden_size // 2
        self.memory_size = memory_size or hidden_size
        self.feature_size = feature_size
        self.dropout = dropout
        self.concat = concat

        self.rnn_input_size = self.input_size
        self.out_input_size = self.hidden_size
        self.cue_input_size = self.hidden_size

        if self.feature_size is not None:
            self.rnn_input_size += self.feature_size
            self.cue_input_size += self.feature_size

        if self.attn_mode is not None:
            self.attention = Attention(query_size=self.hidden_size,
                                       memory_size=self.memory_size,
                                       hidden_size=self.attn_hidden_size,
                                       mode=self.attn_mode,
                                       project=False)
            self.rnn_input_size += self.memory_size
            self.cue_input_size += self.memory_size
            self.out_input_size += self.memory_size

        self.rnn = nn.GRU(input_size=self.rnn_input_size,
                          hidden_size=self.hidden_size,
                          num_layers=self.num_layers,
                          dropout=self.dropout if self.num_layers > 1 else 0,
                          batch_first=True)

        self.cue_rnn = nn.GRU(input_size=self.cue_input_size,
                              hidden_size=self.hidden_size,
                              num_layers=self.num_layers,
                              dropout=self.dropout if self.num_layers > 1 else 0,
                              batch_first=True)

        self.fc1 = nn.Linear(self.hidden_size, self.hidden_size)
        self.fc2 = nn.Linear(self.hidden_size, self.hidden_size)
        if self.concat:
            self.fc3 = nn.Linear(self.hidden_size * 2, self.hidden_size)
        else:
            self.fc3 = nn.Linear(self.hidden_size * 2, 1)
        self.tanh = nn.Tanh()
        self.sigmoid = nn.Sigmoid()

        if self.out_input_size > self.hidden_size:
            self.output_layer = nn.Sequential(
                nn.Dropout(p=self.dropout),
                nn.Linear(self.out_input_size, self.hidden_size),
                nn.Linear(self.hidden_size, self.output_size),
                nn.LogSoftmax(dim=-1),
            )
        else:
            self.output_layer = nn.Sequential(
                nn.Dropout(p=self.dropout),
                nn.Linear(self.out_input_size, self.output_size),
                nn.LogSoftmax(dim=-1),
            )

    def initialize_state(self,
                         hidden,
                         feature=None,
                         attn_memory=None,
                         attn_mask=None,
                         memory_lengths=None,
                         knowledge=None):
        """
        initialize_state
        """
        if self.feature_size is not None:
            assert feature is not None

        if self.attn_mode is not None:
            assert attn_memory is not None

        if memory_lengths is not None and attn_mask is None:
            max_len = attn_memory.size(1)
            attn_mask = sequence_mask(memory_lengths, max_len).eq(0)

        init_state = DecoderState(
            hidden=hidden,
            feature=feature,
            attn_memory=attn_memory,
            attn_mask=attn_mask,
            knowledge=knowledge,
        )
        return init_state

    def decode(self, input, state, is_training=False):
        """
        decode
        """
        hidden = state.hidden
        rnn_input_list = []
        cue_input_list = []
        out_input_list = []
        output = Pack()

        if self.embedder is not None:
            input = self.embedder(input)

        # shape: (batch_size, 1, input_size)
        input = input.unsqueeze(1)
        rnn_input_list.append(input)
        cue_input_list.append(state.knowledge)

        if self.feature_size is not None:
            feature = state.feature.unsqueeze(1)
            rnn_input_list.append(feature)
            cue_input_list.append(feature)

        if self.attn_mode is not None:
            attn_memory = state.attn_memory
            attn_mask = state.attn_mask
            query = hidden[-1].unsqueeze(1)
            weighted_context, attn = self.attention(query=query,
                                                    memory=attn_memory,
                                                    mask=attn_mask)
            rnn_input_list.append(weighted_context)
            cue_input_list.append(weighted_context)
            out_input_list.append(weighted_context)
            output.add(attn=attn)

        rnn_input = torch.cat(rnn_input_list, dim=-1)
        rnn_output, rnn_hidden = self.rnn(rnn_input, hidden)

        cue_input = torch.cat(cue_input_list, dim=-1)
        cue_output, cue_hidden = self.cue_rnn(cue_input, hidden)

        h_y = self.tanh(self.fc1(rnn_hidden))
        h_cue = self.tanh(self.fc2(cue_hidden))
        if self.concat:
            new_hidden = self.fc3(torch.cat([h_y, h_cue], dim=-1))
        else:
            k = self.sigmoid(self.fc3(torch.cat([h_y, h_cue], dim=-1)))
            new_hidden = k * h_y + (1 - k) * h_cue
        out_input_list.append(new_hidden.transpose(0, 1))

        out_input = torch.cat(out_input_list, dim=-1)
        state.hidden = new_hidden

        if is_training:
            return out_input, state, output
        else:
            log_prob = self.output_layer(out_input)
            return log_prob, state, output

    def forward(self, inputs, state):
        """
        forward
        """
        inputs, lengths = inputs
        batch_size, max_len = inputs.size()

        out_inputs = inputs.new_zeros(
            size=(batch_size, max_len, self.out_input_size),
            dtype=torch.float)

        # sort by lengths
        sorted_lengths, indices = lengths.sort(descending=True)
        inputs = inputs.index_select(0, indices)
        state = state.index_select(indices)

        # number of valid input (i.e. not padding index) in each time step
        num_valid_list = sequence_mask(sorted_lengths).int().sum(dim=0)

        for i, num_valid in enumerate(num_valid_list):
            dec_input = inputs[:num_valid, i]
            valid_state = state.slice_select(num_valid)
            out_input, valid_state, _ = self.decode(
                dec_input, valid_state, is_training=True)
            state.hidden[:, :num_valid] = valid_state.hidden
            out_inputs[:num_valid, i] = out_input.squeeze(1)

        # Resort
        _, inv_indices = indices.sort()
        state = state.index_select(inv_indices)
        out_inputs = out_inputs.index_select(0, inv_indices)

        log_probs = self.output_layer(out_inputs)
        return log_probs, state
