################################################################################
# Script 8.2:                                                                  #
# DECODER 2                                                                    #
################################################################################

import glob
import random
import editdistance
import numpy as np
import scipy
import torch
import torchaudio

def seed_everything(seed):      
    random.seed(seed)
    np.random.seed(seed)
    torch.manual_seed(seed)
    torch.cuda.manual_seed(seed)
    torch.backends.cudnn.deterministic = True
    torch.backends.cudnn.benchmark = True

seed_everything(42)

## ENCODER #####################################################################
## FeedForward -----------------------------------------------------------------
class FeedForward(torch.nn.Module):
    def __init__(self, d_model=512, d_ff=1024, dropout=0.1, **kwargs):
        super().__init__()
        self.ff = torch.nn.Sequential(
            torch.nn.LayerNorm(d_model),
            torch.nn.Linear(d_model, d_ff),
            torch.nn.ReLU(),
            torch.nn.Dropout(dropout),
            torch.nn.Linear(d_ff, d_model),
        )
        
    def forward(self, x):
        return self.ff(x)
    
## Self attention  -------------------------------------------------------------
class SelfAttention(torch.nn.Module):
    def __init__(self, d_model, n_heads=8, d_head=64, dropout=0.1, **kwargs):
        super().__init__()
        self.d_model = d_model
        self.n_heads = n_heads
        self.d_head = d_head
        self.scale = torch.sqrt(torch.tensor(d_head, dtype=torch.float32))
        self.norm = torch.nn.LayerNorm(d_model)
        self.q_linear = torch.nn.Linear(d_model, d_head*n_heads)
        self.v_linear = torch.nn.Linear(d_model, d_head*n_heads)
        self.k_linear = torch.nn.Linear(d_model, d_head*n_heads)
        self.dropout = torch.nn.Dropout(dropout)
        self.out = torch.nn.Linear(d_head*n_heads, d_model)

    def forward(self, x):
        x = self.norm(x)
        b = x.shape[0]
        q = self.q_linear(x).view(b, -1, self.n_heads, self.d_head)
        k = self.k_linear(x).view(b, -1, self.n_heads, self.d_head)
        v = self.v_linear(x).view(b, -1, self.n_heads, self.d_head) 

        scores = torch.einsum('bihd,bjhd->bhij', q, k) / self.scale
     
        att = scores.softmax(dim=-1)
        att = self.dropout(att)

        out = torch.einsum('bhij,bjhd->bihd', att, v).reshape(b, -1, 
                                                    self.n_heads*self.d_head)
        out = self.dropout(out)
        out = self.out(out)
        
        return out

## Encoder ---------------------------------------------------------------------
class Encoder(torch.nn.Module):
    def __init__(self, nb_layers=6, seq_len=400, **kwargs):
        super().__init__()        
        self.pos = torch.nn.Parameter(
            torch.randn(1, seq_len, kwargs['d_model']))
        self.att = torch.nn.ModuleList(
            [SelfAttention(**kwargs) for _ in range(nb_layers)])
        self.ff = torch.nn.ModuleList(
            [FeedForward(**kwargs) for _ in range(nb_layers)])
        
    def forward(self, x):
        b, t, d = x.shape
        x = x + self.pos[:, :t, :]
        for att, ff in zip(self.att, self.ff):
            x = x + att(x)
            x = x + ff(x)   

        return x
    
## DECODER #####################################################################
## Causal self attention -------------------------------------------------------
class CausalSelfAttention(torch.nn.Module):
    def __init__(self, d_model, n_heads=8, d_head=64, dropout=0.1, seq_len=400, 
                 **kwargs):
        super().__init__()
        self.d_model = d_model
        self.n_heads = n_heads
        self.d_head = d_head
        self.scale = torch.sqrt(torch.tensor(d_head, dtype=torch.float32))
        self.norm = torch.nn.LayerNorm(d_model)
        self.q_linear = torch.nn.Linear(d_model, d_head*n_heads)
        self.v_linear = torch.nn.Linear(d_model, d_head*n_heads)
        self.k_linear = torch.nn.Linear(d_model, d_head*n_heads)
        self.dropout = torch.nn.Dropout(dropout)
        self.out = torch.nn.Linear(d_head*n_heads, d_model)
        
        self.register_buffer("mask", torch.tril(
            torch.ones(seq_len, seq_len)).view(1, 1, seq_len, seq_len) == 0)

    def forward(self, x):
        x = self.norm(x)
        b, n, d = x.shape
        q = self.q_linear(x).view(b, -1, self.n_heads, self.d_head)
        k = self.k_linear(x).view(b, -1, self.n_heads, self.d_head)
        v = self.v_linear(x).view(b, -1, self.n_heads, self.d_head) 

        scores = torch.einsum('bihd,bjhd->bhij', q, k) / self.scale
        
        scores = scores.masked_fill(self.mask[:,:,:n,:n], float('-inf'))
        att = scores.softmax(dim=-1)
        att = self.dropout(att)

        out = torch.einsum('bhij,bjhd->bihd', att, v).reshape(b, -1, 
                                                    self.n_heads*self.d_head)
        out = self.dropout(out)
        out = self.out(out)

        return out

## Cross attention -------------------------------------------------------------
class CrossAttention(torch.nn.Module):
    def __init__(self, d_model, n_heads=8, d_head=64, dropout=0.1, **kwargs):
        super().__init__()
        self.d_model = d_model
        self.n_heads = n_heads
        self.d_head = d_head
        self.scale = torch.sqrt(torch.tensor(d_head, dtype=torch.float32))
        self.norm1 = torch.nn.LayerNorm(d_model)
        self.norm2 = torch.nn.LayerNorm(d_model)
        self.q_linear = torch.nn.Linear(d_model, d_head*n_heads)
        self.v_linear = torch.nn.Linear(d_model, d_head*n_heads)
        self.k_linear = torch.nn.Linear(d_model, d_head*n_heads)
        self.dropout = torch.nn.Dropout(dropout)
        self.out = torch.nn.Linear(d_head*n_heads, d_model)
    
    def forward(self, x1, x2):
        x1 = self.norm1(x1)
        x2 = self.norm2(x2)  
        b = x1.shape[0]
        q = self.q_linear(x1).view(b, -1, self.n_heads, self.d_head)
        k = self.k_linear(x2).view(b, -1, self.n_heads, self.d_head)
        v = self.v_linear(x2).view(b, -1, self.n_heads, self.d_head) 

        scores = torch.einsum('bihd,bjhd->bhij', q, k) / self.scale
     
        att = scores.softmax(dim=-1)
        att = self.dropout(att)

        out = torch.einsum('bhij,bjhd->bihd', att, v).reshape(b, -1, 
                                                    self.n_heads*self.d_head)
        out = self.dropout(out)
        out = self.out(out)

        return out, att

## Decoder ---------------------------------------------------------------------
class Decoder(torch.nn.Module):
    def __init__(self, nb_layers=6, seq_len=400, **kwargs):
        super().__init__()        
        self.pos = torch.nn.Parameter(
            torch.randn(1, seq_len, kwargs['d_model']))
        self.att = torch.nn.ModuleList(
            [CausalSelfAttention(**kwargs) for _ in range(nb_layers)])
        self.cross_att = torch.nn.ModuleList(
            [CrossAttention(**kwargs) for _ in range(nb_layers)])
        self.ff = torch.nn.ModuleList(
            [FeedForward(**kwargs) for _ in range(nb_layers)])
        
    def forward(self, x, enc):
        b, t, d = x.shape
        x = x + self.pos[:, :t, :]
        for att, cross_att, ff in zip(self.att, self.cross_att, self.ff):
            x = x + att(x)
            x = x + cross_att(x, enc)[0]
            x = x + ff(x)            

        return x
    
## AUDIO PROCESSING     ########################################################
## (feature extraction) ########################################################
## Mel spectrogram -------------------------------------------------------------
class SpecAug(torch.nn.Module):
    def __init__(self, prob_t_warp=0.5,
                       t_factor=(0.9, 1.1), 
                       f_mask_width = (0, 8), 
                       t_mask_width = (0, 10),
                       nb_f_masks=[1,2], 
                       nb_t_masks=[1,2], 
                       ):
        super().__init__()
        self.t_factor = t_factor
        self.f_mask_width = f_mask_width
        self.t_mask_width = t_mask_width
        self.nb_f_masks = nb_f_masks
        self.nb_t_masks = nb_t_masks
        self.prob_t_warp = prob_t_warp

    def time_warp(self, x):
        x = torch.nn.functional.interpolate(x, size=
                        (int(x.shape[2]*np.random.uniform(*self.t_factor)), ))

        return x
    
    def freq_mask(self, x):
        for _ in range(np.random.randint(*self.nb_f_masks)):
            f = np.random.randint(*self.f_mask_width)
            f0 = np.random.randint(0, x.shape[1]-f)

            x[:,f0:f0+f,:] = 0
        return x

    def time_mask(self, x):
        for _ in range(np.random.randint(*self.nb_t_masks)):
            t = np.random.randint(*self.t_mask_width)
            t0 = np.random.randint(0, x.shape[2]-t)

            x[:,:,t0:t0+t] = 0
        return x

    def forward(self, x):
        if np.random.uniform() < self.prob_t_warp:
            x = self.time_warp(x)
        x = self.freq_mask(x)
        x = self.time_mask(x)
        return x
    
## Feature extractor -----------------------------------------------------------
## The feature extractor is composed of a log mel-spectrogram and a linear layer
class AudioFeatures(torch.nn.Module):
    def __init__(self, feat_dim=80, d_model=512, **kwargs):
        super().__init__()
        self.fe = torchaudio.transforms.MelSpectrogram(
                        n_fft=512, 
                        win_length=25*16, 
                        hop_length=10*16, 
                        n_mels=feat_dim)  # 25ms window, 
                                          # 10ms shift
        self.spec_aug = SpecAug()
        self.linear = torch.nn.Linear(feat_dim, d_model)

    def forward(self, x): 
        x = self.fe(x)        
        x = (x+1e-6).log()
        if self.training:
            x = self.spec_aug(x)
        x = x.transpose(1, 2)
        x = self.linear(x)
        return x
    
## FULL TRANSFORMER ############################################################
class AudioTransformer(torch.nn.Module):
    def __init__(self, vocab_size=24, **kwargs):
        super().__init__()
        self.vocab_size = vocab_size
        self.seq_len = kwargs['seq_len']

        self.fe = AudioFeatures(**kwargs)
        self.enc = Encoder(**kwargs)
        
        self.emb = torch.nn.Embedding(vocab_size, kwargs['d_model'])
        self.dec = Decoder(**kwargs)
        self.out = torch.nn.Linear(kwargs['d_model'], vocab_size)
        
    def encoder(self, x):
        # with feature extractor
        x = self.fe(x)
        
        return self.enc(x)

    def decoder(self, y, enc):
        y = self.emb(y)
        dec = self.dec(y, enc)

        return self.out(dec)
    
    def forward(self, x, y):
        enc = self.encoder(x)

        return self.decoder(y, enc)
                
    def loss(self, x, y):        
        logits = self(x, y[:,:-1])
        target = y[:,1:]
        loss = torch.nn.functional.cross_entropy(logits.reshape(-1, 
                                                self.vocab_size), 
                                                target.reshape(-1))
        return loss
    
    def generate(self, x):
        device = next(self.parameters()).device
        self.eval()
        x = torch.nn.functional.pad(x.clone().detach(), (0, 
                                                         self.seq_len-len(x)), 
                                                         value=23)        
        if len(x.shape) == 1:
            x = x[None, :]
        y = [20,]
        with torch.no_grad():            
            enc = self.encoder(x.to(device))   
            
            while y[-1] != 22 and len(y) < self.seq_len:
                logits = self.decoder(torch.tensor(y).unsqueeze(0).to(device), 
                                      enc)
                y.append(logits.argmax(-1)[:,-1].item())
                            
        return y

## DATA AUGMENTATION ###########################################################
def identity(x):
    return x
## Noise augmentation ----------------------------------------------------------
class NoiseAug(object):
    def __init__(self, noise_dir='data/musan_small/', prob=0.5):
        self.prob = prob
        self.noises = glob.glob(noise_dir+'/*/*.wav')
        
    def __call__(self, x):
        if np.random.uniform() < self.prob:
            n = torchaudio.load( np.random.choice(self.noises) )[0][0]            
            if len(n) < len(x):
                n = torch.nn.functional.pad(n, (0, len(x)-len(n)), value=0)
            elif len(n) > len(x):
                t0 = np.random.randint(0, len(n) - len(x))
                n = n[t0:t0+len(x)]
            n = n.numpy()
            p_x = x.std()**2
            p_n = n.std()**2
            snr = np.random.uniform(5, 15)
            n = n * np.sqrt(p_x/p_n) * np.power(10, -snr/20)
            x = x + n

        return x
    
## RIR augmentation ------------------------------------------------------------
class RIRAug(object):
    def __init__(self, rir_dir='data/RIRS_NOISES_small/simulated_rirs_small/', 
                 prob=0.5):
        self.prob = prob
        self.rirs = glob.glob(rir_dir+'/*.wav') 

    def __call__(self, x):
        if np.random.uniform() < self.prob:
            n = len(x)
            rir = torchaudio.load( np.random.choice(self.rirs) )[0][0]
            rir = rir.numpy()
            rir = rir / np.max(np.abs(rir))
            x = scipy.signal.convolve(x, rir)
            t0 = np.argmax(np.abs(rir))
            x = x[t0:t0+n]

        return x
    
## DATA ########################################################################
def num2list(n):
    return [int(d) for d in str(n)]

## Dataset ---------------------------------------------------------------------
class DigitsumDataset(torch.utils.data.Dataset):
    def __init__(self, data_dir=None, audio_len=4*16000, 
                 transform=[identity], seq_len=20, flag='train'):
        self.transform = transform
        self.audio_len = audio_len
        self.seq_len = seq_len
        self.files = sorted(glob.glob(data_dir+'/*.wav'))
        self.flag = flag

        print("- Dataset size:", len(self.files), "files\n")

    def __len__(self):
        return len(self.files)
    
    def __getitem__(self, idx):
        x, _ = torchaudio.load(self.files[idx])
        if x.shape[1] < self.audio_len:
            x = torch.nn.functional.pad(x, (0, self.audio_len-x.shape[1]), 
                                        value=0)
        else:
            x = x[:, :self.audio_len]
        

        if self.flag == 'train':
            x = x[0].numpy()
            for t in self.transform:
                x = t(x)
        else:
            x = x[0]
        
        # get the number by the name of the file
        number = self.files[idx].split('.')[-2].split('_')[-1]
        number = number.replace('o', '0')
        number = [int(d) for d in str(number)] # now number is a list of digits
        sum_number = num2list(sum(number)) # sum of digits in a list

        if len(number) == 1:
            y = [20, ] + number + [19, ] + number + [21, ] + sum_number + [22]
        elif len(number) == 2:
            y = [20, ] + number + [19, ] + num2list(sum(number)) + [21, ] + sum_number + [22]
        elif len(number) == 3:
            y = [20, ] + number + [19, ] + num2list(number[0] + number[1]) + [18, ]  + [number[2]] + [21, ] + sum_number + [22]
        else:
            y = [20, ] + number + [19, ] + num2list(number[0] + number[1]) + [18, ]  + num2list(number[2] + number[3]) + [21, ] + sum_number + [22]

        y = torch.nn.functional.pad(torch.tensor(y), (0, self.seq_len-len(y)), 
                                    value=23)

        return x, y

print("# TRAINING DATA #")
# - with data augmentation
trainset = DigitsumDataset(data_dir='data/data3/train',
                           transform=[NoiseAug(), RIRAug()], flag = 'train')
# - without data augmentation
# trainset = DigitsumDataset(data_dir='data/data3/train', flag='train')
print("#   TEST DATA   #")
testset = DigitsumDataset(data_dir='data/data3/test', flag='test')

# TRAINING #####################################################################

model = AudioTransformer(vocab_size=24, d_model=256, nb_layers=8, 
                         d_ff=512, n_heads=8, d_head=32, dropout=0.1, 
                         seq_len=500)

print("#  TRAINING...  #")
device = 'cuda'
model.to(device)
opt = torch.optim.Adam(model.parameters(), lr=3e-4)

nb_epochs = 75
batch_size = 32
model.train()

trainloader = torch.utils.data.DataLoader(trainset, batch_size=batch_size, 
                                          shuffle=True)
for e in range(nb_epochs):
    loss_sum = 0
    for x, y in trainloader:
        x = x.to(device)
        y = y.to(device)
        opt.zero_grad()
        loss = model.loss(x, y)
        loss.backward()
        opt.step()
        loss_sum += loss.item()
        break
    print(f'epoch {e} loss {loss_sum/len(trainloader):.2f}')
print("-> Training finished.\n")

# print("# SAVING MODEL... #")
# torch.save(model.state_dict(), 'prac1.pt')
# print("-> Model saved.\n")

# TESTING ######################################################################
print("#    TESTING...   #")
model.eval()
err = 0
num = 0
for i,(x, y) in enumerate(testset):    
    x = x.to(device)    
    y_pred = model.generate(x[None,...])
    hyp = ' '.join([str(i) for i in y_pred[1:y_pred.index(22)]])
    y = y.numpy().tolist()
    y = y[:y.index(22)] ## find the first 22 in y
    ref = ' '.join([str(i) for i in y[1:]])  
    # edit distance
    # print('(%d/%d)' % (i, len(testset)) )
    # print('ref', ref)
    # print('hyp', hyp)  
    err += editdistance.eval(hyp, ref)
    num += len(ref.split())
    
print(f'error rate {err/num:.2%},  ({err}/{num})')