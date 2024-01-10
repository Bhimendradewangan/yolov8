# Ultralytics YOLO 🚀, AGPL-3.0 license

from multiprocessing.pool import ThreadPool
from pathlib import Path

import numpy as np
import torch
import torch.nn.functional as F

from ultralytics.models.yolo.detect import DetectionValidator
from ultralytics.utils import LOGGER, NUM_THREADS, ops
from ultralytics.utils.checks import check_requirements
from ultralytics.utils.metrics import OKS_SIGMA, MultiTaskMetrics, box_iou, mask_iou, kpt_iou
from ultralytics.utils.plotting import output_to_target, plot_images


class MultiTaskValidator(DetectionValidator):
    """
    A class extending the DetectionValidator class for validation based on a segmentation model.

    Example:
        ```python
        from ultralytics.models.yolo.segment import SegmentationValidator

        args = dict(model='yolov8n-seg.pt', data='coco8-seg.yaml')
        validator = SegmentationValidator(args=args)
        validator()
        ```
    """

    def __init__(self, dataloader=None, save_dir=None, pbar=None, args=None, _callbacks=None):
        """Initialize SegmentationValidator and set task to 'segment', metrics to SegmentMetrics."""
        super().__init__(dataloader, save_dir, pbar, args, _callbacks)
        self.plot_masks = None
        self.process = None
        self.sigma = None
        self.kpt_shape = None
        self.args.task = 'multitask'
        self.metrics = MultiTaskMetrics(save_dir=self.save_dir, on_plot=self.on_plot)
        if isinstance(self.args.device, str) and self.args.device.lower() == 'mps':
            LOGGER.warning("WARNING ⚠️ Apple MPS known Pose bug. Recommend 'device=cpu' for Pose models. "
                           'See https://github.com/ultralytics/ultralytics/issues/4031.')

    def preprocess(self, batch):
        """Preprocesses batch by converting masks to float and sending to device."""
        batch = super().preprocess(batch)
        batch['masks'] = batch['masks'].to(self.device).float()
        batch['keypoints'] = batch['keypoints'].to(self.device).float()
        return batch

    def init_metrics(self, model):
        """Initialize metrics and select mask processing function based on save_json flag."""
        super().init_metrics(model)
        self.plot_masks = []
        if self.args.save_json:
            check_requirements('pycocotools>=2.0.6')
            self.process = ops.process_mask_upsample  # more accurate
        else:
            self.process = ops.process_mask  # faster
        self.kpt_shape = self.data['kpt_shape']

        is_pose = self.kpt_shape == [17, 3]
        nkpt = self.kpt_shape[0]
        self.sigma = OKS_SIGMA if is_pose else np.ones(nkpt) / nkpt

    def get_desc(self):
        """Return a formatted description of evaluation metrics."""
        return ('%22s' + '%11s' * 10) % ('Class', 'Images', 'Instances', 'Box(P', 'R', 'mAP50', 'mAP50-95)', 'Mask(P',
                                         'R', 'mAP50', 'mAP50-95)')

    def postprocess(self, preds):
        """Post-processes YOLO predictions and returns output detections with proto."""
        p_seg = ops.non_max_suppression(preds[0][0],
                                    self.args.conf,
                                    self.args.iou,
                                    labels=self.lb,
                                    multi_label=True,
                                    agnostic=self.args.single_cls,
                                    max_det=self.args.max_det,
                                    nc=self.nc)
        p_pose = ops.non_max_suppression(preds[0][1] if len(preds[1]) == 4 else preds[1],
                                    self.args.conf,
                                    self.args.iou,
                                    labels=self.lb,
                                    multi_label=True,
                                    agnostic=self.args.single_cls,
                                    max_det=self.args.max_det,
                                    nc=self.nc)
        proto = preds[1][-2] if len(preds[1]) == 4 else preds[0][1]  # second output is len 4 if pt, but only 1 if exported
        # with open(r'C:\Users\david\Downloads\kpt.txt', 'a') as f:
        #         f.write(f'\nValidation Postprocess\n')
        #         f.write(f'preds[0][0].shape: {preds[0][0].shape}\n')
        #         f.write(f'preds[0][1].shape: {preds[0][1].shape}\n')
        #         f.write(f'p_seg.shape: {p_seg[0].shape}\n')
        #         f.write(f'p_pose.shape: {p_pose[0].shape}\n')
        return (p_seg, p_pose), proto

    def update_metrics(self, preds, batch):
        """Metrics."""
        for si, (pred_seg, pred_kpt, proto) in enumerate(zip(preds[0][0], preds[0][1], preds[1])):

            idx = batch['batch_idx'] == si
            cls = batch['cls'][idx]
            bbox = batch['bboxes'][idx]
            kpts = batch['keypoints'][idx]
            nl, nprs = cls.shape[0], pred_seg.shape[0]  # number of labels, segmentation predictions
            nprk = pred_kpt.shape[0]  # number of predicted keypoints
            nk = kpts.shape[1]  # number of keypoints
            kdim = kpts.shape[2]  # keypoint dimension (x, y, v)
            shape = batch['ori_shape'][si]
            correct_kpts = torch.zeros(nprk, self.niou, dtype=torch.bool, device=self.device)  # init
            correct_masks = torch.zeros(nprs, self.niou, dtype=torch.bool, device=self.device)  # init
            correct_bboxes = torch.zeros(nprs, self.niou, dtype=torch.bool, device=self.device)  # init
            self.seen += 1

            with open(r'C:\Users\david\Downloads\kpt.txt', 'a') as f:
                f.write("\nProto Val\n")
                f.write(f'pred_seg.shape: {pred_seg.shape}\n')
                f.write(f'pred_kpt.shape: {pred_kpt.shape}\n')
                f.write(f'proto.shape: {proto.shape}\n')
            # with open(r'C:\Users\david\Downloads\kpt.txt', 'a') as f:
            #     f.write(f'\nValidation\n')
            #     f.write(f'kpts.shape: {kpts.shape}\n')
            #     f.write(f'len(pred_kpt): {len(pred_kpt)}\n')
            #     f.write("using preds[0][1]\n" if len(preds[1]) == 4 else 'using preds[1]\n')
            #     f.write(f'len(pred_seg); {len(pred_seg)}\n')
            #     f.write(f'pred_seg[0].shape: {pred_seg[0].shape}\n')
            #     f.write(f'len(pred_kpt): {len(pred_kpt)}\n')
            #     f.write(f'pred_kpt[0].shape: {pred_kpt[0].shape}\n')
            
            if nprs == 0:
                if nl:
                    self.stats.append((correct_bboxes, correct_masks, correct_kpts *torch.zeros(
                        (2, 0), device=self.device), cls.squeeze(-1)))
                    if self.args.plots:
                        self.confusion_matrix.process_batch(detections=None, labels=cls.squeeze(-1))
                continue

            # Masks
            midx = [si] if self.args.overlap_mask else idx
            gt_masks = batch['masks'][midx]
            pred_masks = self.process(proto, pred_seg[:, 6:], pred_seg[:, :4], shape=batch['img'][si].shape[1:])

            # Predictions
            if self.args.single_cls:
                pred_seg[:, 5] = 0
                pred_kpt[:, 5] = 0

            predn_seg = pred_seg.clone()
            predn_kpt = pred_kpt.clone()

            ops.scale_boxes(batch['img'][si].shape[1:], predn_seg[:, :4], shape,
                            ratio_pad=batch['ratio_pad'][si])  # native-space pred
            pred_kpts = predn_kpt[:, 6:].view(nprk, nk, -1)
            ops.scale_coords(batch['img'][si].shape[1:], pred_kpts, shape, ratio_pad=batch['ratio_pad'][si])

            # Evaluate
            if nl:
                height, width = batch['img'].shape[2:]
                tbox = ops.xywh2xyxy(bbox) * torch.tensor(
                    (width, height, width, height), device=self.device)  # target boxes
                ops.scale_boxes(batch['img'][si].shape[1:], tbox, shape,
                                ratio_pad=batch['ratio_pad'][si])  # native-space labels
                tkpts = kpts.clone()
                tkpts[..., 0] *= width
                tkpts[..., 1] *= height
                tkpts = ops.scale_coords(batch['img'][si].shape[1:], tkpts, shape, ratio_pad=batch['ratio_pad'][si])
                labelsn = torch.cat((cls, tbox), 1)  # native-space labels
                correct_bboxes = self._process_batch(predn_seg, labelsn)
                # TODO: maybe remove these `self.` arguments as they already are member variable
                correct_masks = self._process_batch(predn_seg,
                                                    labelsn,
                                                    pred_masks,
                                                    gt_masks,
                                                    overlap=self.args.overlap_mask,
                                                    masks=True)
                correct_kpts = self._process_batch(predn_kpt[:, :6], labelsn, pred_kpts, tkpts)
                if self.args.plots:
                    self.confusion_matrix.process_batch(predn_seg, labelsn)

            # Append correct_masks, correct_boxes, pconf, pcls, tcls
            self.stats.append((correct_bboxes, correct_masks, correct_kpts, predn_seg[:, 4], predn_seg[:, 5], cls.squeeze(-1)))

            pred_masks = torch.as_tensor(pred_masks, dtype=torch.uint8)
            if self.args.plots and self.batch_i < 3:
                self.plot_masks.append(pred_masks[:self.args.max_det].cpu())  # filter top 15 to plot

            # Save
            if self.args.save_json:
                pred_masks = ops.scale_image(pred_masks.permute(1, 2, 0).contiguous().cpu().numpy(),
                                             shape,
                                             ratio_pad=batch['ratio_pad'][si])
                self.pred_to_json(predn_seg, batch['im_file'][si], pred_masks)
            # if self.args.save_txt:
            #    save_one_txt(predn, save_conf, shape, file=save_dir / 'labels' / f'{path.stem}.txt')

    def finalize_metrics(self, *args, **kwargs):
        """Sets speed and confusion matrix for evaluation metrics."""
        self.metrics.speed = self.speed
        self.metrics.confusion_matrix = self.confusion_matrix

    def _process_batch(self, detections, labels, pred_kpts=None, pred_masks=None, gt_masks=None, gt_kpts=None, overlap=False, masks=False):
        """
        Return correct prediction matrix.

        Args:
            detections (torch.Tensor): Tensor of shape [N, 6] representing detections.
                Each detection is of the format: x1, y1, x2, y2, conf, class.
            labels (torch.Tensor): Tensor of shape [M, 5] representing labels.
                Each label is of the format: class, x1, y1, x2, y2.
            pred_kpts (torch.Tensor, optional): Tensor of shape [N, 51] representing predicted keypoints.
                51 corresponds to 17 keypoints each with 3 values.
            gt_kpts (torch.Tensor, optional): Tensor of shape [N, 51] representing ground truth keypoints.

        Returns:
            correct (array[N, 10]), for 10 IoU levels
        """
        if pred_kpts is not None and gt_kpts is not None:
            # `0.53` is from https://github.com/jin-s13/xtcocoapi/blob/master/xtcocotools/cocoeval.py#L384
            area = ops.xyxy2xywh(labels[:, 1:])[:, 2:].prod(1) * 0.53
            iou = kpt_iou(gt_kpts, pred_kpts, sigma=self.sigma, area=area)
        
        if masks and pred_kpts is not None and gt_kpts is not None:
            if overlap:
                nl = len(labels)
                index = torch.arange(nl, device=gt_masks.device).view(nl, 1, 1) + 1
                gt_masks = gt_masks.repeat(nl, 1, 1)  # shape(1,640,640) -> (n,640,640)
                gt_masks = torch.where(gt_masks == index, 1.0, 0.0)
            if gt_masks.shape[1:] != pred_masks.shape[1:]:
                gt_masks = F.interpolate(gt_masks[None], pred_masks.shape[1:], mode='bilinear', align_corners=False)[0]
                gt_masks = gt_masks.gt_(0.5)
            mask_iou = mask_iou(gt_masks.view(gt_masks.shape[0], -1), pred_masks.view(pred_masks.shape[0], -1))

            # `0.53` is from https://github.com/jin-s13/xtcocoapi/blob/master/xtcocotools/cocoeval.py#L384
            area = ops.xyxy2xywh(labels[:, 1:])[:, 2:].prod(1) * 0.53
            point_iou = kpt_iou(gt_kpts, pred_kpts, sigma=self.sigma, area=area)

            iou = (mask_iou*0.6 + point_iou*0.4).clamp(min=0, max=1)
        else:  # boxes
            iou = box_iou(labels[:, 1:], detections[:, :4])

        return self.match_predictions(detections[:, 5], labels[:, 0], iou)

    def plot_val_samples(self, batch, ni):
        """Plots validation samples with bounding box labels."""
        plot_images(batch['img'],
                    batch['batch_idx'],
                    batch['cls'].squeeze(-1),
                    batch['bboxes'],
                    batch['masks'],
                    kpts=batch['keypoints'],
                    paths=batch['im_file'],
                    fname=self.save_dir / f'val_batch{ni}_labels.jpg',
                    names=self.names,
                    on_plot=self.on_plot)

    def plot_predictions(self, batch, preds, ni):
        """Plots batch predictions with masks and bounding boxes."""    
        if len(preds[1]) == 4:
            pred_kpts = torch.cat([p[:, 6:].contiguous().view(-1, *self.kpt_shape) for p in preds[0][1]], 0)
            batch_idx, cls, bboxes = output_to_target(preds[0][1], max_det=self.args.max_det)
        else:
            pred_kpts = torch.cat([p[:, 6:].contiguous().view(-1, *self.kpt_shape) for p in preds[1]], 0)
            batch_idx, cls, bboxes = output_to_target(preds[1], max_det=self.args.max_det)

        plot_images(batch['img'],
                    batch_idx,
                    cls,
                    bboxes,
                    masks=torch.cat(self.plot_masks, dim=0) if len(self.plot_masks) else self.plot_masks,
                    kpts=pred_kpts,
                    paths=batch['im_file'],
                    fname=self.save_dir / f'val_batch{ni}_pred.jpg',
                    names=self.names,
                    on_plot=self.on_plot)  # pred
        self.plot_masks.clear()

    def pred_to_json(self, predn, filename, pred_masks):
        """Save one JSON result."""
        # Example result = {"image_id": 42, "category_id": 18, "bbox": [258.15, 41.29, 348.26, 243.78], "score": 0.236}
        from pycocotools.mask import encode  # noqa

        def single_encode(x):
            """Encode predicted masks as RLE and append results to jdict."""
            rle = encode(np.asarray(x[:, :, None], order='F', dtype='uint8'))[0]
            rle['counts'] = rle['counts'].decode('utf-8')
            return rle

        stem = Path(filename).stem
        image_id = int(stem) if stem.isnumeric() else stem
        box = ops.xyxy2xywh(predn[:, :4])  # xywh
        box[:, :2] -= box[:, 2:] / 2  # xy center to top-left corner
        pred_masks = np.transpose(pred_masks, (2, 0, 1))
        with ThreadPool(NUM_THREADS) as pool:
            rles = pool.map(single_encode, pred_masks)
        for i, (p, b) in enumerate(zip(predn.tolist(), box.tolist())):
            self.jdict.append({
                'image_id': image_id,
                'category_id': self.class_map[int(p[5])],
                'bbox': [round(x, 3) for x in b],
                'keypoints': p[6:],
                'score': round(p[4], 5),
                'segmentation': rles[i]})

    def eval_json(self, stats):
        """Return COCO-style object detection evaluation metrics."""
        if self.args.save_json and self.is_coco and len(self.jdict):
            anno_json = self.data['path'] / 'annotations/instances_val2017.json'  # annotations
            pred_json = self.save_dir / 'predictions.json'  # predictions
            LOGGER.info(f'\nEvaluating pycocotools mAP using {pred_json} and {anno_json}...')
            try:  # https://github.com/cocodataset/cocoapi/blob/master/PythonAPI/pycocoEvalDemo.ipynb
                check_requirements('pycocotools>=2.0.6')
                from pycocotools.coco import COCO  # noqa
                from pycocotools.cocoeval import COCOeval  # noqa

                for x in anno_json, pred_json:
                    assert x.is_file(), f'{x} file not found'
                anno = COCO(str(anno_json))  # init annotations api
                pred = anno.loadRes(str(pred_json))  # init predictions api (must pass string, not Path)
                for i, eval in enumerate([COCOeval(anno, pred, 'bbox'), COCOeval(anno, pred, 'segm'), COCOeval(anno, pred, 'keypoints')]):
                    if self.is_coco:
                        eval.params.imgIds = [int(Path(x).stem) for x in self.dataloader.dataset.im_files]  # im to eval
                    eval.evaluate()
                    eval.accumulate()
                    eval.summarize()
                    idx = i * 4 + 2
                    stats[self.metrics.keys[idx + 1]], stats[
                        self.metrics.keys[idx]] = eval.stats[:2]  # update mAP50-95 and mAP50
            except Exception as e:
                LOGGER.warning(f'pycocotools unable to run: {e}')
        return stats
