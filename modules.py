import torch
import torch.nn as nn
import torch.nn.functional as F
from torch.autograd import Variable
import math

class CSF(nn.Module):
    def __init__(self, img_size, h_size, latent_dim, output_size, block_count):#img_size=[C,H,W]
        super(CSF,self).__init__()
        # x为(batch_size,36,2048) or (batch_size,2048), y为(batch_size,2048) => (batch_size,36,o) or (batch_size,o)
        self.att_c_mfh = MFH(x_size=img_size[1]*img_size[2], y_size=h_size, latent_dim=latent_dim, output_size=output_size,block_count=block_count)
        self.att_c_net = nn.Sequential(
            nn.Linear(output_size*block_count, 512),
            nn.Tanh(),
            nn.Linear(512, 1))

        # x为(batch_size,36,2048) or (batch_size,2048), y为(batch_size,2048) => (batch_size,36,o) or (batch_size,o)
        self.att_s_mfh = MFH(x_size=img_size[0], y_size=h_size, latent_dim=latent_dim, output_size=output_size,block_count=block_count)
        self.att_s_net = nn.Sequential(
            nn.Linear(output_size*block_count, 512),
            nn.Tanh(),
            nn.Linear(512, 1))

    def forward(self,img,h):#(bs,C,7,7) (bs,h_size) => (bs,C,7,7)
        #c
        img_size=[*img.size()]#list [bs,C,7,7] C=512 or 2048
        img=img.view(img_size[0], img_size[1], img_size[2]*img_size[3])#(bs, C, H, W) => (bs, C, H*W) (bs, C, 49)
        img = F.normalize(img, p=2, dim=2)#(bs, C, 49)已经是49个region的image attention feature,这里是feature vector内部normalize

        att=self.att_c_mfh(img,h)#(bs, C, 49), (bs,512) => (bs, C, o) o=2048 here
        att=self.att_c_net(att)#(bs, C, o) => (bs, C, 1)

        att=F.softmax(att,dim=1).squeeze(2).permute(1,0)#(bs, C, 1) => (bs, C) => (C, bs)
        img=img.permute(2,1,0)#(bs, C, 49) => (49, C, bs)
        img=img*att#(49, C, bs)*(C, bs) => (49, C, bs)
        img = img.permute(2, 0, 1)#(bs, 49, C)

        #s
        img = F.normalize(img, p=2,dim=2)# (bs, 49, C)已经是49个region的image attention feature,这里是feature vector内部normalize
        att=self.att_s_mfh(img,h)#(bs, 49, C), (bs,512) => (bs, 49, o) o=2048 here
        att=self.att_s_net(att)#(bs, 49, o) => (bs, 49, 1)

        att=F.softmax(att,dim=1).squeeze(2).permute(1,0)#(bs, 49, 1) => (bs, 49) => (49, bs)
        img=img.permute(2,1,0)#(bs, 49, C) => (C, 49, bs)
        img=img*att#(C, 49, bs)*(49, bs) => (C, 49, bs)

        img=img.permute(2,0,1).contiguous()#view之前一定要把tensor的memory用contiguous()放在一起
        img=img.view(*img_size)#(C, 49, bs) => (bs, C, 49) => (bs,C,7,7)
        return img

class CS(nn.Module):
    def __init__(self, img_size, h_size, k_size=512):#img_size=[C,H,W]
        super(CS,self).__init__()

        self.avgpool=nn.AvgPool1d(kernel_size=img_size[1]*img_size[2],stride=1)

        #自定义参数 channel-wise attention
        self.wc = nn.Parameter(torch.Tensor(k_size, 1), requires_grad=True) #(k,1)
        self.bc = nn.Parameter(torch.Tensor(k_size, 1), requires_grad=True) #(k,1)
        self.whc = nn.Parameter(torch.Tensor(k_size, h_size), requires_grad=True) #(k,h)
        self.wci = nn.Parameter(torch.Tensor(1, k_size), requires_grad=True) #(1,k)
        self.bci = nn.Parameter(torch.Tensor(1, 1), requires_grad=True) #(1,1)

        #自定义参数 spacial-wise attention
        self.ws = nn.Parameter(torch.Tensor(k_size, img_size[0]), requires_grad=True) #(k,C)
        self.bs = nn.Parameter(torch.Tensor(k_size, 1), requires_grad=True) #(k,1)
        self.whs = nn.Parameter(torch.Tensor(k_size, h_size), requires_grad=True) #(k,h)
        self.wsi = nn.Parameter(torch.Tensor(1, k_size), requires_grad=True) #(1,k)
        self.bsi = nn.Parameter(torch.Tensor(1, 1), requires_grad=True) #(1,1)

        #initialization
        self.init_parameters()

    def init_parameters(self):
        #init c
        nn.init.xavier_uniform(self.wc)
        nn.init.xavier_uniform(self.bc)
        nn.init.xavier_uniform(self.whc)
        nn.init.xavier_uniform(self.wci)
        nn.init.xavier_uniform(self.bci)
        #init s
        nn.init.xavier_uniform(self.ws)
        nn.init.xavier_uniform(self.bs)
        nn.init.xavier_uniform(self.whs)
        nn.init.xavier_uniform(self.wsi)
        nn.init.xavier_uniform(self.bsi)


    def forward(self,img,h):#(bs,C,7,7) (bs,h_size) => (bs,C,7,7)
        #c
        img_size=[*img.size()]#list [bs,C,7,7] C=512 or 2048
        img=img.view(img_size[0], img_size[1], img_size[2]*img_size[3])#(bs, C, H, W) => (bs, C, H*W) (bs, C, 49)

        #channel-wise attention
        img_channel = F.normalize(img, p=2, dim=2)#(bs, C, 49)已经是49个region的image attention feature,这里是feature vector内部normalize

        img_channel=self.avgpool(img_channel)#(bs, C, 49) => (bs, C, 1)
        img_channel=img_channel.squeeze(2).unsqueeze(1)#(bs, C, 1) => (bs, C) => (bs, 1, C)

        tmp1=torch.matmul(self.wc,img_channel)# (k,1)*(bs, 1, C)=>(bs, k, C)
        tmp1=tmp1+self.bc#(bs, k, C)+ (k,1) => (bs, k, C)

        tmp2=torch.matmul(self.whc,h.unsqueeze(2))#(bs, k, h) => (bs, h, 1) => (bs, k, 1)
        tmp3=F.tanh(tmp1+tmp2)#(bs, k, C) + (bs, k, 1) = (bs, k, C)
        tmp3 = torch.matmul(self.wci, tmp3).squeeze(1)  #(1,k) * (bs, k, C) => (bs, 1, C) => (bs, C)
        tmp3= tmp3+self.bci #(bs, C) + (1,1) => (bs, C)
        att=F.softmax(tmp3, dim=1)#(bs, C)

        #channel-wise attention using
        att = att.permute(1, 0)  #(bs, C) => (C, bs)
        img = img.permute(2, 1, 0)  # (bs, C, 49) => (49, C, bs)
        img = img * att  # (49, C, bs)*(C, bs) => (49, C, bs)
        img = img.permute(2, 0, 1)  # (bs, 49, C)

        #spacial-wise attention
        img_spacial = F.normalize(img, p=2,dim=2).permute(0,2,1) # (bs, 49, C) => (bs, C, 49)已经是49个region的image attention feature,这里是feature vector内部normalize
        tmp1 = torch.matmul(self.ws, img_spacial)  # (k,C)*(bs, C, 49)=>(bs, k, 49)
        tmp1=tmp1+self.bs #(bs, k, 49) + (k,1) => (bs, k, 49)

        tmp2 = torch.matmul(self.whs, h.unsqueeze(2))  # (bs, k, h) => (bs, h, 1) => (bs, k, 1)
        tmp3=F.tanh(tmp1+tmp2)#(bs, k, 49) + (bs, k, 1) = (bs, k, 49)
        tmp3 = torch.matmul(self.wsi, tmp3).squeeze(1)  #(1,k) * (bs, k, 49) => (bs, 1, 49) => (bs, 49)
        tmp3= tmp3+self.bsi #(bs, 49) + (1,1) => (bs, 49)
        att = F.softmax(tmp3, dim=1)  # (bs, 49)

        #spacial-wise attention using
        att = att.permute(1, 0)  #(bs, 49) => (49, bs)
        img = img.permute(2, 1, 0)  # (bs, 49, C) => (C, 49, bs)
        img = img * att  # (C, 49, bs)*(49, bs) => (C, 49, bs)

        img=img.permute(2,0,1).contiguous()#(C, 49, bs)=>(bs, C, 49) view之前一定要把tensor的memory用contiguous()放在一起
        img=img.view(*img_size)#(bs, C, 49) => (bs,C,7,7)
        return img


class MFH(nn.Module):# x为(batch_size,36,2048) or (batch_size,2048), y为(batch_size,2048) => (batch_size,36,o) or (batch_size,o)
    # 2048, 512, latent_dim=4, output_size=1024, block_count=2
    #image vector m, question vector n, k, output_sizeo,
    # MFH(2048, 512, latent_dim=4, output_size=1024, block_count=2)#(batch_size,36,o) or (batch_size,1,o)
    def __init__(self, x_size, y_size, latent_dim, output_size, block_count, dropout=0.1):
        super(MFH, self).__init__()
        hidden_size = latent_dim * output_size#k*o
        self.x2hs = nn.ModuleList([
            nn.Linear(x_size, hidden_size) for i in range(block_count)])
        self.y2hs = nn.ModuleList([
            nn.Linear(y_size, hidden_size) for i in range(block_count)])
        self.dps = nn.ModuleList([
            nn.Dropout(dropout) for i in range(block_count)])

        self.latent_dim = latent_dim
        self.output_size = output_size
        self.hidden_size = hidden_size
        self.block_count = block_count

#在取attention时，x为(batch_size,36,2048)，y为(batch_size,2048)，为了之后concat方便，把y加一维成(batch_size,1,2048)
#在不取attention时 x=att_img(batch_size,2048), y=hn(batch_size,512)
    @staticmethod
    def align_dim(x, y):#将x, y的维度调成一致，若x的维度多于y，则将多出来的维度插到y的第0维之后，且增添的维度全为1，若y的维度多于x，同理
        max_dims = x.size()
        if x.dim() > y.dim():
            diff_dim = [1,] * (x.dim() - y.dim())
            y_size = list(y.size())
            new_y_size = y_size[:1] + diff_dim + y_size[1:]
            y = y.view(*new_y_size)#Returns a new tensor with the same data as the self tensor but of a different size.
            max_dims = x.size()
        elif x.dim() < y.dim():
            diff_dim = [1,] * (y.dim() - x.dim())
            x_size = x.size()
            new_x_size = x_size[:1] + diff_dim + x_size[1:]
            x = x.view(*new_x_size)
            max_dims = y.size()
        return x, y, list(max_dims)#维数相同的x和y，原先维数比较大的size()，(batch_size,36,2048)，(batch_size,1,2048)，(batch_size,36,2048)


    def forward(self, x, y):#MFB中block_count=1，MFH中block_count>=1 x is the attention image part feature vector, y is queation feature vector
        x, y, max_dims = self.align_dim(x, y)#(batch_size,36,2048)，(batch_size,1,2048)，(batch_size,36,2048)
        #Returns this self tensor cast to the type of the given tensor.
        #Returns a tensor filled with the scalar value 1, with the shape defined by the varargs sizes
        last_exp = Variable(torch.ones(self.hidden_size).type_as(x.data))#k*o
        exp_size = max_dims[:-1] + [self.output_size, self.latent_dim]#(batch_size,36,o,k)
        results = []
        for i in range(self.block_count):
            #nn.Linear() Input: (N,∗,in_features) where * means any number of additional dimensions
            xh = self.x2hs[i](x)#(batch_size,36,k*o) mfh question feature vector m->k*o
            yh = self.y2hs[i](y)#(batch_size,1,k*o) mfh image feature vecctor n->k*o
            last_exp = last_exp * self.dps[i](xh * yh)# *为element-wise product, 若某个维度的长度没有对齐，但是是整数倍，则自动补齐#(batch_size,36,k*o)
            #上面一句和原版不一样
            #view():Returns a new tensor with the same data but different size.
            z_sum = last_exp.view(exp_size).sum(dim=-1)#(batch_size,36,k*o)->(batch_size,36,o,k)->(batch_size,36,o) sumpooling
            z_sqrt = z_sum.sign() * (z_sum.abs() + 1e-7).sqrt()#power normalize #(batch_size,36,o)
            z_norm = F.normalize(z_sqrt, p=2, dim=-1)#在每个长为o的vector上做normalize #(batch_size,36,o) #L2 normalize

            results.append(z_norm)#(block_count, batch_size,36,o)

        return torch.cat(results, dim=-1)#(batch_size,36,o) or (batch_size,o)


class GatedTanh(nn.Module):
    def __init__(self, in_size, out_size, bias=True, use_conv=False):
        super(GatedTanh, self).__init__()
        if use_conv:
            self.fc = nn.Conv1d(in_size, out_size, kernel_size=1, bias=bias)
            self.gate_fc = nn.Conv1d(in_size, out_size, kernel_size=1, bias=bias)
        else:
            self.fc = nn.Linear(in_size, out_size, bias=bias)
            self.gate_fc = nn.Linear(in_size, out_size, bias=bias)

    def forward(self, input):
        return F.tanh(self.fc(input)) * F.sigmoid(self.gate_fc(input))

