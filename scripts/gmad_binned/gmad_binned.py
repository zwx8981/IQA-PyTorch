#!/usr/bin/env python3
"""Select a maximally disagreeing image pair at each defender quality level.

This standalone companion preserves pyiqa's APIs and CLI. Selection is linear in
the pool size and does not enumerate all possible image pairs. Human preference
collection and the original gMAD experimental protocol are outside its scope.
"""

import argparse
import json
import math
from bisect import bisect_right
from pathlib import Path


def _quality_scale(scores, lower_better, score_range):
    """Map a metric's raw scores to [0, 1], with higher meaning better."""
    scores = [float(score) for score in scores]
    if not all(math.isfinite(score) for score in scores):
        raise ValueError('All scores must be finite')
    observed = [min(scores), max(scores)]
    if score_range is None:
        lower, upper = observed
    else:
        if len(score_range) != 2:
            raise ValueError('Score ranges require exactly two endpoints')
        lower, upper = map(float, score_range)
    if not all(math.isfinite(value) for value in (lower, upper, upper - lower)):
        raise ValueError('Score range and its width must be finite')
    if lower > upper:
        raise ValueError('Score range endpoints must be in ascending raw-score order')
    if observed[0] < lower or observed[1] > upper:
        raise ValueError('Scores fall outside the specified score range')
    if lower == upper:
        quality = [0.5] * len(scores)
    elif lower_better:
        quality = [(upper - score) / (upper - lower) for score in scores]
    else:
        quality = [(score - lower) / (upper - lower) for score in scores]
    return scores, quality, {
        'lower_better': lower_better,
        'raw_score_range': [lower, upper],
        'range_source': 'candidate_pool' if score_range is None else 'explicit',
        'observed_raw_score_range': observed,
        'degenerate_range': lower == upper,
    }


def select_binned_pairs(
    images, scores_a, scores_b, metric_a, metric_b, *,
    lower_better_a=False, lower_better_b=False, bins=6,
    score_range_a=None, score_range_b=None,
):
    """Return one maximum attacker-gap pair per defender bin, in both roles.

    Bins cover the defender's quality scale in equal-width intervals. Each
    interval includes its lower bound and excludes its upper bound, except
    the last interval, which includes 1. Images may participate in both roles;
    repeated raw scores stay in the same bin. Equal optimum gaps are resolved
    deterministically by input order.
    """
    if isinstance(bins, bool) or not isinstance(bins, int) or bins < 1:
        raise ValueError('bins must be a positive integer')
    if not metric_a or not metric_b or metric_a == metric_b:
        raise ValueError('Two different nonempty metric names are required')
    if len(images) < 2 or len(images) != len(scores_a) or len(images) != len(scores_b):
        raise ValueError('At least two images with equally sized score lists are required')
    paths = [str(Path(image).resolve()) for image in images]
    if len(set(paths)) != len(paths):
        raise ValueError('Image paths must be unique')
    raw_a, quality_a, calibration_a = _quality_scale(scores_a, lower_better_a, score_range_a)
    raw_b, quality_b, calibration_b = _quality_scale(scores_b, lower_better_b, score_range_b)
    edges = [level / bins for level in range(bins + 1)]

    def image_result(index):
        return {
            'path': paths[index],
            'scores': {metric_a: raw_a[index], metric_b: raw_b[index]},
            'quality_scores': {metric_a: quality_a[index], metric_b: quality_b[index]},
        }

    def direction(defender, attacker, defender_raw, attacker_raw,
                  defender_quality, attacker_quality, calibration, attacker_lower_better):
        groups = [[] for _ in range(bins)]
        for index, quality in enumerate(defender_quality):
            level = min(bins - 1, bisect_right(edges, quality) - 1)
            groups[level].append(index)
        lower, upper = calibration['raw_score_range']
        span = upper - lower
        levels = []
        for level, indices in enumerate(groups):
            start, stop = edges[level:level + 2]
            if calibration['lower_better']:
                raw_bounds = [upper - span * stop, upper - span * start]
            else:
                raw_bounds = [lower + span * start, lower + span * stop]
            result = {
                'level': level + 1,
                'quality_interval': {
                    'lower': start, 'upper': stop,
                    'lower_inclusive': True, 'upper_inclusive': level == bins - 1,
                },
                'defender_raw_interval': {
                    'lower': raw_bounds[0], 'upper': raw_bounds[1],
                    'lower_inclusive': not calibration['lower_better'] or level == bins - 1,
                    'upper_inclusive': calibration['lower_better'] or level == bins - 1,
                },
                'image_count': len(indices),
                'status': 'insufficient_images',
                'pair': None,
            }
            if calibration['degenerate_range']:
                result['defender_raw_interval'] = None
            if len(indices) >= 2:
                # Select opposite attacker extrema; input-index tie-breaking
                # keeps two distinct images even when every score is equal.
                def attacker_order(index):
                    score = attacker_raw[index]
                    return (-score if attacker_lower_better else score), index

                first = min(indices, key=attacker_order)
                second = max(indices, key=attacker_order)
                gap = abs(attacker_raw[first] - attacker_raw[second])
                result['status'] = 'selected' if gap > 0 else 'zero_attacker_gap'
                result['pair'] = {
                    'images': [image_result(first), image_result(second)],
                    'image_order': 'attacker_lower_quality_then_higher_quality',
                    'defender_gap_raw': abs(defender_raw[first] - defender_raw[second]),
                    'defender_gap_quality': abs(defender_quality[first] - defender_quality[second]),
                    'attacker_gap_raw': gap,
                    'attacker_gap_quality': abs(attacker_quality[first] - attacker_quality[second]),
                }
            levels.append(result)
        return {'defender': defender, 'attacker': attacker, 'levels': levels}

    directions = [
        direction(metric_a, metric_b, raw_a, raw_b, quality_a, quality_b, calibration_a, lower_better_b),
        direction(metric_b, metric_a, raw_b, raw_a, quality_b, quality_a, calibration_b, lower_better_a),
    ]
    pairs = [level['pair'] for item in directions for level in item['levels'] if level['pair']]
    unique_pairs = {
        tuple(sorted(image['path'] for image in pair['images'])) for pair in pairs
    }
    warnings = [
        f'{name}: all raw scores are identical; mapped to quality 0.5, '
        'which cannot establish an absolute quality level'
        for name, calibration in ((metric_a, calibration_a), (metric_b, calibration_b))
        if calibration['degenerate_range']
    ]
    return {
        'method': 'equal-width quality-binned gMAD-style candidate search',
        'image_count': len(images),
        'bins_per_direction': bins,
        'quality_order': 'low_to_high',
        'pair_count': len(pairs),
        'unique_pair_count': len(unique_pairs),
        'calibration': {metric_a: calibration_a, metric_b: calibration_b},
        'warnings': warnings,
        'directions': directions,
    }


def run_binned_gmad(
    *, image_dir=None, scores_csv=None, image_root=None,
    metric_a='musiq', metric_b='brisque', direction_a=None, direction_b=None,
    device=None, bins=6, score_range_a=None, score_range_b=None,
):
    """Reuse IQA-PyTorch's input readers/inference, then run external selection."""
    if bool(image_dir) == bool(scores_csv):
        raise ValueError('Provide exactly one of image_dir or scores_csv')
    if metric_a == metric_b:
        raise ValueError('The two metrics must be different')
    if isinstance(bins, bool) or not isinstance(bins, int) or bins < 1:
        raise ValueError('bins must be a positive integer')
    for value in (direction_a, direction_b):
        if value not in (None, 'lower', 'higher'):
            raise ValueError('Metric direction must be lower or higher')

    # These private helpers belong to the inspected repository version. Keep
    # this small adapter separate from the model-independent selection logic.
    from pyiqa.gmad_test import (
        _collect_images,
        _load_score_csv,
        _resolve_lower_better,
        _score_images,
    )

    if scores_csv:
        images, scores_a, scores_b = _load_score_csv(
            scores_csv, metric_a, metric_b, image_root=image_root,
        )
        lower_a = _resolve_lower_better(
            metric_a, None if direction_a is None else direction_a == 'lower',
        )
        lower_b = _resolve_lower_better(
            metric_b, None if direction_b is None else direction_b == 'lower',
        )
        source = 'precomputed scores'
    else:
        if image_root:
            raise ValueError('image_root only applies to scores_csv')
        if direction_a is not None or direction_b is not None:
            raise ValueError('Direction overrides only apply to scores_csv; inference uses model metadata')
        images = _collect_images(image_dir)
        model, scores_a = _score_images(metric_a, images, device)
        lower_a = model.lower_better
        del model
        model, scores_b = _score_images(metric_b, images, device)
        lower_b = model.lower_better
        del model
        source = 'pyiqa inference'
    result = select_binned_pairs(
        images, scores_a, scores_b, metric_a, metric_b,
        lower_better_a=lower_a, lower_better_b=lower_b, bins=bins,
        score_range_a=score_range_a, score_range_b=score_range_b,
    )
    result['score_source'] = source
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument('--scores-csv', help='CSV columns: image, <metric-a>, <metric-b>')
    source.add_argument('--image-dir', help='Score an image directory using two pyiqa NR metrics')
    parser.add_argument('--image-root', help='Base for relative CSV image paths; default: CSV directory')
    parser.add_argument('--metric-a', default='musiq')
    parser.add_argument('--metric-b', default='brisque')
    parser.add_argument('--direction-a', choices=('lower', 'higher'), help='Custom CSV score direction')
    parser.add_argument('--direction-b', choices=('lower', 'higher'), help='Custom CSV score direction')
    parser.add_argument('--bins', type=int, default=6, help='Equal-width quality bins per defender (default: 6)')
    parser.add_argument('--range-a', type=float, nargs=2, metavar=('MIN', 'MAX'), help='Fixed raw-score limits for A')
    parser.add_argument('--range-b', type=float, nargs=2, metavar=('MIN', 'MAX'), help='Fixed raw-score limits for B')
    parser.add_argument('--device', help='pyiqa inference device, e.g. cpu or cuda')
    parser.add_argument('-o', '--output', help='Write JSON to a file; default: stdout')
    args = parser.parse_args(argv)
    if args.output and args.scores_csv and Path(args.output).resolve() == Path(args.scores_csv).resolve():
        parser.error('Output must not overwrite the input CSV')
    try:
        result = run_binned_gmad(
            image_dir=args.image_dir, scores_csv=args.scores_csv, image_root=args.image_root,
            metric_a=args.metric_a, metric_b=args.metric_b,
            direction_a=args.direction_a, direction_b=args.direction_b,
            device=args.device, bins=args.bins, score_range_a=args.range_a, score_range_b=args.range_b,
        )
    except (ValueError, OSError) as error:
        parser.error(str(error))
    output = json.dumps(result, indent=2, ensure_ascii=False, allow_nan=False) + '\n'
    if args.output:
        Path(args.output).write_text(output, encoding='utf-8')
    else:
        print(output, end='')


if __name__ == '__main__':
    main()
