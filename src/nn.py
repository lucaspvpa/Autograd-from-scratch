﻿from typing import Any
import framework
import numpy as np
from tensor import Tensor, Parameter

class Module:
    ''' General Module superclass'''
    def __init__(self):
        pass

    def __call__(self, x):
        return self.forward(x)

    def parameters(self):
        '''
        Returns all model parameters in a list. Iterates over each item in self.__dict__,
        and returns every Parameter object, or Tensor objects with requires_grad set to True.

        @returns params (list): All parameters in the model.
        '''
        params = []
        for _, param in self.__dict__.items():
            if isinstance(param, Module):
                params += param.parameters()
            elif isinstance(param, Parameter):
                params.append(param)
            elif isinstance(param, Tensor):
                if param.requires_grad:
                    params.append(param)
        return params

    def train(self):
        ''' Sets module's mode to train, which influences layers like Dropout'''
        self.mode = 'train'
        for _, param in self.__dict__.items():
            if isinstance(param, Module):
                param.train()


    def eval(self):
        ''' Sets module's mode to eval, which influences layers like Dropout'''
        self.mode = 'eval'
        for _, param in self.__dict__.items():
            if isinstance(param, Module):
                param.eval()

# Base Layers:
class Linear(Module):
    ''' Simple linear layer, with weight matrix and optional bias. Does not contain nonlinearity. '''
    def __init__(self, in_size: int, out_size: int, bias: bool = True):
        '''
        @param in_size (int): size of the last dimention of the input array.
        @param out_size (int): size of the last dimention of the output array.
        @param bias (bool): wether to include a bias term.
        '''
        super().__init__()
        self.W = framework.tensor(np.random.randn(in_size, out_size) / np.sqrt(in_size), requires_grad=True)
        self.b = framework.tensor(np.zeros(out_size), requires_grad=True)
        self.has_bias = bias

    def forward(self, x):
        z = x @ self.W 
        if self.has_bias:
            z += self.b
        return z


class MultiHeadSelfAttention(Module):
    ''' Full Transformer Layer implementation. '''
    def __init__(self, in_size: int, out_size: int, n_heads: int, n_timesteps: int, dropout_prob: float=0):
        '''
        @param in_size (int): size of the last dimention of the input array.
        @param out_size (int): size of the last dimention of the output array.
        @param n_heads (int): number of parallel heads to be computed (must equally divide in_size).
        @param n_timesteps (int): length of text sequence to be processed bt Transformer.
        @param dropout_prob (float): probability of zeroing each activation in dropout Layer.
        '''
        super().__init__()
        self.Wk = Linear(in_size, in_size, bias=False)
        self.Wq = Linear(in_size, in_size, bias=False)
        self.Wv = Linear(in_size, in_size, bias=False)
        self.residual_proj = Linear(in_size, out_size)
        self.mask = np.triu(np.ones((n_timesteps,n_timesteps)).reshape(1,1,n_timesteps,n_timesteps), k=1)
        self.att_dropout = Dropout(dropout_prob)
        self.residual_dropout = Dropout(dropout_prob)
        self.softmax = Softmax()

        self.H = in_size // n_heads # head_size
        assert in_size % n_heads==0, "embedding dimension not divisible in equal heads."

    def forward(self, x):
        B, T, D = x.shape
        H = self.H
        nh = D//H
        # Get key, queries and values from the input:
        k = self.Wk(x) # (B, T, D) @ (D, D) -> (B, T, D)
        q = self.Wq(x) # (B, T, D) @ (D, D) -> (B, T, D)
        v = self.Wv(x) # (B, T, D) @ (D, D) -> (B, T, D)
        
        # Reshape into different heads:
        k = k.reshape(B,T,nh,H).transpose(1,2) # (B, T, D) -> (B, T, nh, H) -> (B, nh, T, H)
        q = q.reshape(B,T,nh,H).transpose(1,2) # (B, T, D) -> (B, T, nh, H) -> (B, nh, T, H)
        v = v.reshape(B,T,nh,H).transpose(1,2) # (B, T, D) -> (B, T, nh, H) -> (B, nh, T, H)

        # Compute attention activation:
        att = q @ k.transpose(-2, -1) # (B, nh, T, H) @ (B, nh, H, T) -> (B, nh, T, T)

        # Reduce module before going into softmax:
        att = att / H**(.5)

        # Apply mask (to block out future characters), softmax, and dropout:
        att = att.masked_fill(self.mask[:,:,:T,:T] == 0, float('-inf'))
        att = self.softmax(att, dim=-1)
        att = self.att_dropout(att)

        # Compute weighted sum between values:
        out = att @ v # (B, nh, T, T) @ (B, nh, T, H) -> (B, nh, T, H)

        # Restack heads in D dimension:
        out = out.transpose(1, 2).reshape(B, T, D) # (B, nh, T, H) -> (B, T, D)

        # Apply final projection (Dense layer) and dropout:
        out = self.residual_proj(out) # (B, T, D) @ (D, D) -> (B, T, D)
        out = self.residual_dropout(out)

        self.cache = (att, k, v, q)
        return out

# Embedding Layers
class Embedding(Module):
    def __init__(self, in_size, embed_size):
        super().__init__()
        
        self.E = framework.tensor(np.random.randn(in_size, embed_size) / np.sqrt(in_size), requires_grad=True)


    def forward(self, idx):
        # Extracts embedding from row "idx":
        x = self.E[idx._data]

        self.cache = (idx)
        return x


class PositionalEmbedding(Module):
    def __init__(self, n_timesteps, embed_size):
        super().__init__()
        self.E = framework.tensor(np.random.randn(n_timesteps, embed_size) / np.sqrt(n_timesteps), requires_grad=True)


    def forward(self, x):
        B, T = x.shape
        # Adds positional embeddings to input of size (batch_size,n_timesteps,embedding_dim):
        x = self.E[:T]
        return x

# Regularization Layers:
class Dropout(Module):
    def __init__(self,drop_prob):
        super().__init__()
        self.p = drop_prob
        self.mode = 'train'
   
    def forward(self,z):
        if self.mode == 'eval':
            return z
        mask = framework.rand(z.shape) > self.p
        a = z.masked_fill(mask==False, 0) 
        a = a / (1 - self.p)
        return a


class LayerNorm(Module):
    def __init__(self, n_embed):
        super().__init__()
        self.gamma = framework.ones([1, n_embed], requires_grad=True)
        self.beta = framework.zeros([1, n_embed], requires_grad=True)
    

    def forward(self,x):
        var = framework.var(x, dim=-1, keepdims=True) # (B, T)
        norm = (x - framework.mean(x, dim=-1, keepdims=True)) / framework.sqrt(var) # (B, T, D)
        z = norm * self.gamma + self.beta # (B, T, D)
        return z

# Non-Linearity Layers:
class ReLU(Module):
    def __init__(self):
        super().__init__()

    def forward(self, z):
        mask = framework.Tensor(np.where(z._data < 0, 0, 1))
        z = z * mask
        return z


class Softmax(Module):
    def __init__(self):
        super().__init__()

    def __call__(self, x, dim=-1):
        return self.forward(x, dim)

    def forward(self, z, dim=-1):
        z = framework.exp(z)
        out = z / framework.sum(z, dim=dim, keepdims=True)
        return out


class Tanh(Module):
    def __init__(self):
        super().__init__()

    def forward(self, z):
        z = framework.exp(z)
        z_neg = framework.exp(-z)
        out = (z - z_neg) / (z + z_neg)
        return out

# Composed Layers:
class FullyConnected(Module):
    def __init__(self, in_size, out_size, dropout_prob=0):
        '''
        @param in_size (int): size of the last dimention of the input array.
        @param out_size (int): size of the last dimention of the output array.
        @param dropout_prob (float): probability of zeroing each activation in dropout Layer.
        '''
        super().__init__()
        self.in_size = in_size

        self.fcc1 = Linear(in_size, in_size * 4)
        self.relu = ReLU()
        self.fcc2 = Linear(in_size * 4, out_size)
        self.dropout = Dropout(dropout_prob)


    def forward(self, x):
        z = self.fcc1(x)
        z = self.relu(z)
        z = self.fcc2(z)
        z = self.dropout(z)
        return z


class Block(Module):
    def __init__(self, in_size: int, out_size: int, n_heads: int, n_timesteps: int, dropout_prob: float=0):
        '''
        @param in_size (int): size of the last dimention of the input array.
        @param out_size (int): size of the last dimention of the output array.
        @param n_heads (int): number of parallel heads to be computed (must equally divide in_size).
        @param n_timesteps (int): length of text sequence to be processed bt Transformer.
        @param dropout_prob (float): probability of zeroing each activation in dropout Layer.
        '''
        super().__init__()
        self.att = MultiHeadSelfAttention(in_size, in_size, n_heads, n_timesteps, dropout_prob)
        self.ln1 = LayerNorm(in_size)
        self.fcc = FullyConnected(in_size, out_size, dropout_prob)
        self.ln2 = LayerNorm(out_size)
        

    def forward(self,x):
        x = x + self.att(self.ln1(x))
        z = x + self.fcc(self.ln2(x))
        return z

# Loss Layer:
class CrossEntropyLoss(Module):
    def __init__(self):
        super().__init__()

    def __call__(self, z, y):
        return self.forward(z, y)

    def forward(self, z, y):
        *B_dims, D = z.shape
        B = np.prod(B_dims)
        z = z.reshape(B,D)
        
        logits = framework.exp(z)
        logits = logits / framework.sum(logits, dim= 1, keepdims=True)

        y = y.reshape(B)
            
        # get cross-entropy loss:
        log_losses = framework.log(logits[np.arange(B), y])
        loss = -framework.sum(log_losses) / (B)
        return loss
