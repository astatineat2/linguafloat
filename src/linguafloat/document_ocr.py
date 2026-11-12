"""Pinned RapidOCR adapter: propagate classifier rotation into word geometry."""
from __future__ import annotations

import numpy as np
from rapidocr import RapidOCR


def undo_classifier_rotation(points, quad):
    import cv2
    quad = np.asarray(quad, dtype=np.float32)
    unit = np.array([[0, 0], [1, 0], [1, 1], [0, 1]], np.float32)
    forward = cv2.getPerspectiveTransform(quad, unit)
    inverse = cv2.getPerspectiveTransform(unit, quad)
    mapped = cv2.perspectiveTransform(np.asarray(points, np.float32)[None, ...], forward)
    mapped = 1 - mapped
    return cv2.perspectiveTransform(mapped, inverse)[0]


class DocumentRapidOCR(RapidOCR):
    def build_final_output(self, ori_img, det_res, cls_res, rec_res, cropped_img_list, op_record):
        decisions = getattr(cls_res, "cls_res", None) or ()
        threshold = self.text_cls.cls_thresh
        self._word_rotations = [
            i < len(decisions) and "180" in decisions[i][0] and decisions[i][1] > threshold
            for i, text in enumerate(rec_res.txts or ()) if text.strip()]
        try:
            return super().build_final_output(ori_img, det_res, cls_res, rec_res, cropped_img_list, op_record)
        finally:
            self._word_rotations = ()

    def calc_word_boxes(self, img, dt_boxes, rec_res):
        rec_res = self.cal_rec_boxes(img, dt_boxes, rec_res, self.return_single_char_box)
        corrected = []
        for i, line in enumerate(rec_res.word_results):
            rotated = i < len(getattr(self, "_word_rotations", ())) and self._word_rotations[i]
            words = tuple((text, score, undo_classifier_rotation(box, dt_boxes[i]) if rotated else box)
                          for text, score, box in line if box is not None)
            corrected.append(words)
        return tuple(corrected)

    def filter_by_text_score(self, ocr_res):
        # Upstream removes empty word rows independently of text rows. Keep the
        # same line indexes through confidence filtering, even without word boxes.
        scores = tuple(ocr_res.scores)
        words = ocr_res.word_results or ()
        aligned = tuple(words[i] if i < len(words) else () for i in range(len(scores)))
        ocr_res.word_results = aligned
        result = super().filter_by_text_score(ocr_res)
        result.word_results = tuple(aligned[i] for i, score in enumerate(scores) if score >= self.text_score)
        return result
