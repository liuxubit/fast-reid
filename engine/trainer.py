# encoding: utf-8
"""
@author:  l1aoxingyu
@contact: sherlockliao01@gmail.com
"""

import logging
import os

import matplotlib.pyplot as plt
import torch.nn.functional as F

from data.datasets.eval_reid import evaluate
from fastai.vision import *


@dataclass
class TrackValue(Callback):
    logger: logging.Logger
    total_iter: int

    def on_epoch_end(self, epoch, smooth_loss, **kwargs):
        self.logger.info(f'Epoch {epoch}[Iter {self.total_iter}], loss: {smooth_loss.item():.4f}')
            

@dataclass
class LRScheduler(Callback):
    learn: Learner
    lr_sched: Scheduler

    def on_train_begin(self, **kwargs:Any):
        self.opt = self.learn.opt

    def on_epoch_begin(self, **kwargs:Any):
        self.opt.lr = self.lr_sched.step()


class TestModel(LearnerCallback):
    def __init__(self, learn: Learner, test_labels: Iterator, eval_period: int, num_query: int, logger: logging.Logger, norm=True):
        super().__init__(learn)
        self._test_dl = learn.data.test_dl
        self._eval_period = eval_period
        self._norm = norm
        self._logger = logger
        self._num_query = num_query
        pids = []
        camids = []
        for i in test_labels:
            pids.append(i[0])
            camids.append(i[1])
        self.q_pids = np.asarray(pids[:num_query])
        self.q_camids = np.asarray(camids[:num_query])
        self.g_pids = np.asarray(pids[num_query:])
        self.g_camids = np.asarray(camids[num_query:])

    def on_epoch_end(self, epoch, **kwargs: Any):
        # test model performance
        if (epoch + 1) % self._eval_period == 0:
            self._logger.info('Testing ...')
            feats, pids, camids = [], [], []
            self.learn.model.eval()
            with torch.no_grad():
                for imgs, _ in self._test_dl:
                    feat = self.learn.model(imgs)
                    feats.append(feat)
            feats = torch.cat(feats, dim=0)
            if self._norm:
                feats = F.normalize(feats, p=2, dim=1)
            # query
            qf = feats[:self._num_query]
            # gallery
            gf = feats[self._num_query:]
            m, n = qf.shape[0], gf.shape[0]
            distmat = torch.pow(qf, 2).sum(dim=1, keepdim=True).expand(m, n) + \
                      torch.pow(gf, 2).sum(dim=1, keepdim=True).expand(n, m).t()
            distmat.addmm_(1, -2, qf, gf.t())
            distmat = to_np(distmat)
            cmc, mAP = evaluate(distmat, self.q_pids, self.g_pids, self.q_camids, self.g_camids)
            self._logger.info(f"Test Results - Epoch: {epoch+1}")
            self._logger.info(f"mAP: {mAP:.1%}")
            for r in [1, 5, 10]:
                self._logger.info(f"CMC curve, Rank-{r:<3}:{cmc[r-1]:.1%}")
            self.learn.save("model_{}".format(epoch))


def do_train(
        cfg,
        model,
        data_bunch,
        test_labels,
        opt_func,
        lr_sched,
        loss_func,
        num_query,
):
    eval_period = cfg.SOLVER.EVAL_PERIOD
    output_dir = Path(cfg.OUTPUT_DIR)
    epochs = cfg.SOLVER.MAX_EPOCHS
    total_iter = len(data_bunch.train_dl)

    logger = logging.getLogger("reid_baseline.train")
    logger.info("Start Training")

    cb_fns = [
        partial(LRScheduler, lr_sched=lr_sched),
        partial(TestModel, test_labels=test_labels, eval_period=eval_period, num_query=num_query, logger=logger),
    ]

    learn = Learner(
        data_bunch,
        model,
        path=output_dir,
        opt_func=opt_func,
        loss_func=loss_func,
        true_wd=False,
        callback_fns=cb_fns,
        callbacks=[TrackValue(logger, total_iter)])

    learn.fit(epochs, lr=cfg.SOLVER.BASE_LR, wd=cfg.SOLVER.WEIGHT_DECAY)
