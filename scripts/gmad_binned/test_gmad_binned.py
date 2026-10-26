import csv
import json
import random
import subprocess
import sys
from itertools import combinations
from pathlib import Path

import pytest
from gmad_binned import run_binned_gmad, select_binned_pairs
from PIL import Image


def paths(count):
    return [f'/candidate/{index}.png' for index in range(count)]


@pytest.mark.parametrize('lower_a,lower_b', [(False, False), (False, True), (True, False), (True, True)])
@pytest.mark.parametrize('bins', [1, 2, 6])
def test_selection_matches_exhaustive_search(lower_a, lower_b, bins):
    rng = random.Random(31)
    scores_a = [rng.randint(-3, 4) for _ in range(37)]
    scores_b = [rng.randint(-5, 5) for _ in range(37)]
    result = select_binned_pairs(
        paths(37), scores_a, scores_b, 'a', 'b', bins=bins,
        lower_better_a=lower_a, lower_better_b=lower_b,
    )
    for direction in result['directions']:
        defender_a = direction['defender'] == 'a'
        defender = scores_a if defender_a else scores_b
        attacker = scores_b if defender_a else scores_a
        lower_better = lower_a if defender_a else lower_b
        low, high = min(defender), max(defender)
        quality = [
            ((high - value) if lower_better else (value - low)) / (high - low)
            for value in defender
        ]
        seen = set()
        for level in direction['levels']:
            lower = (level['level'] - 1) / bins
            upper = level['level'] / bins
            members = [
                index for index, value in enumerate(quality)
                if lower <= value < upper or (level['level'] == bins and value == 1)
            ]
            assert level['image_count'] == len(members)
            seen.update(members)
            if len(members) < 2:
                assert level['pair'] is None
                continue
            expected = max(abs(attacker[i] - attacker[j]) for i, j in combinations(members, 2))
            pair = level['pair']
            assert pair['attacker_gap_raw'] == expected
            selected = [int(Path(image['path']).stem) for image in pair['images']]
            assert len(set(selected)) == 2
            assert set(selected).issubset(members)
            attack_name = direction['attacker']
            assert pair['images'][0]['quality_scores'][attack_name] <= pair['images'][1]['quality_scores'][attack_name]
        assert seen == set(range(37))


def six_level_scores():
    return (
        [value for base in range(5, 60, 10) for value in (base, base + 1)],
        [55, 5, 45, 15, 35, 25, 25, 35, 15, 45, 5, 55],
    )


def test_six_bins_return_twelve_pairs_and_fixed_calibration():
    a, b = six_level_scores()
    result = select_binned_pairs(
        paths(12), a, b, 'a', 'b', score_range_a=(0, 60), score_range_b=(0, 60),
    )
    assert result['pair_count'] == result['unique_pair_count'] == 12
    assert result['calibration']['a']['range_source'] == 'explicit'
    assert [level['image_count'] for level in result['directions'][0]['levels']] == [2] * 6
    assert result['directions'][0]['levels'][0]['defender_raw_interval'] == {
        'lower': 0, 'upper': 10, 'lower_inclusive': True, 'upper_inclusive': False,
    }


@pytest.mark.parametrize('lower_better', [False, True])
def test_boundary_scores_have_single_bin_and_maximum_is_included(lower_better):
    scores = list(range(7))
    result = select_binned_pairs(paths(7), scores, scores, 'a', 'b', lower_better_a=lower_better)
    levels = result['directions'][0]['levels']
    assert [level['image_count'] for level in levels] == [1, 1, 1, 1, 1, 2]
    pair_scores = sorted(image['scores']['a'] for image in levels[-1]['pair']['images'])
    assert pair_scores == ([0, 1] if lower_better else [5, 6])
    interval = levels[-1]['defender_raw_interval']
    assert interval['lower_inclusive'] and interval['upper_inclusive']


def test_identical_defender_scores_stay_together_without_fabricated_quality_levels():
    result = select_binned_pairs(paths(4), [7] * 4, [1, 2, 3, 4], 'a', 'b')
    levels = result['directions'][0]['levels']
    assert [level['image_count'] for level in levels] == [0, 0, 0, 4, 0, 0]
    assert levels[3]['pair']['attacker_gap_raw'] == 3
    assert levels[3]['defender_raw_interval'] is None
    assert result['calibration']['a']['degenerate_range']
    assert result['warnings']


def test_zero_attacker_gap_returns_distinct_images_with_explicit_status():
    result = select_binned_pairs(paths(3), [1, 1, 1], [2, 2, 2], 'a', 'b')
    for direction in result['directions']:
        selected = next(level for level in direction['levels'] if level['pair'])
        assert selected['status'] == 'zero_attacker_gap'
        assert selected['pair']['attacker_gap_raw'] == 0
        assert len({image['path'] for image in selected['pair']['images']}) == 2
    assert result['pair_count'] == 2
    assert result['unique_pair_count'] == 1


@pytest.mark.parametrize('kwargs', [
    {'bins': 0}, {'bins': -1}, {'bins': True}, {'bins': 1.5},
    {'score_range_a': (2, 1)}, {'score_range_a': (0, 0)},
    {'score_range_a': (0, 0.5)}, {'score_range_a': (0, float('inf'))},
])
def test_invalid_configuration_is_rejected(kwargs):
    with pytest.raises(ValueError):
        select_binned_pairs(paths(2), [0, 1], [0, 1], 'a', 'b', **kwargs)


@pytest.mark.parametrize('value', [float('nan'), float('inf'), -float('inf')])
def test_nonfinite_scores_are_rejected(value):
    with pytest.raises(ValueError, match='finite'):
        select_binned_pairs(paths(2), [0, value], [0, 1], 'a', 'b')


def test_shape_and_identity_validation():
    with pytest.raises(ValueError, match='equally sized'):
        select_binned_pairs(paths(2), [0], [0, 1], 'a', 'b')
    with pytest.raises(ValueError, match='unique'):
        select_binned_pairs(['same.png', 'same.png'], [0, 1], [0, 1], 'a', 'b')
    with pytest.raises(ValueError, match='different'):
        select_binned_pairs(paths(2), [0, 1], [0, 1], 'a', 'a')


@pytest.fixture
def score_csv(tmp_path):
    scores_a, scores_b = six_level_scores()
    filename = tmp_path / 'scores.csv'
    with filename.open('w', newline='') as stream:
        writer = csv.writer(stream)
        writer.writerow(['image', 'custom_a', 'custom_b'])
        for index, (a, b) in enumerate(zip(scores_a, scores_b)):
            image = tmp_path / f'{index}.png'
            Image.new('RGB', (2, 2), (index * 20, 80, 100)).save(image)
            writer.writerow([image.name, a, b])
    return filename


def test_existing_csv_reader_and_custom_directions_are_reused(score_csv):
    result = run_binned_gmad(
        scores_csv=score_csv, metric_a='custom_a', metric_b='custom_b',
        direction_a='higher', direction_b='lower',
    )
    assert result['pair_count'] == 12
    assert result['score_source'] == 'precomputed scores'
    assert result['calibration']['custom_b']['lower_better']
    for direction in result['directions']:
        for level in direction['levels']:
            assert all(Path(image['path']).is_file() for image in level['pair']['images'])


def test_known_metric_score_direction_is_inferred(score_csv):
    score_csv.write_text(score_csv.read_text().replace('custom_a,custom_b', 'musiq,brisque'))
    result = run_binned_gmad(scores_csv=score_csv)
    assert result['calibration']['musiq']['lower_better'] is False
    assert result['calibration']['brisque']['lower_better'] is True


def test_cli_writes_json_and_preserves_input_csv(score_csv, tmp_path):
    output = tmp_path / 'result.json'
    script = str(Path(__file__).with_name('gmad_binned.py'))
    command = [
        sys.executable, script, '--scores-csv', str(score_csv),
        '--metric-a', 'custom_a', '--metric-b', 'custom_b',
        '--direction-a', 'higher', '--direction-b', 'lower',
        '--bins', '6', '--output', str(output),
    ]
    completed = subprocess.run(command, capture_output=True, text=True, check=True)
    assert completed.stdout == ''
    assert json.loads(output.read_text())['pair_count'] == 12
    original = score_csv.read_text()
    command[-1] = str(score_csv)
    failed = subprocess.run(command, capture_output=True, text=True, check=False)
    assert failed.returncode != 0
    assert 'must not overwrite' in failed.stderr
    assert score_csv.read_text() == original


def test_missing_csv_image_is_reported(score_csv):
    (score_csv.parent / '0.png').unlink()
    with pytest.raises(ValueError, match='does not exist'):
        run_binned_gmad(
            scores_csv=score_csv, metric_a='custom_a', metric_b='custom_b',
            direction_a='higher', direction_b='lower',
        )


def test_ten_thousand_scores_do_not_require_pair_enumeration():
    count = 10_000
    result = select_binned_pairs(
        paths(count), [index % 100 for index in range(count)],
        [(index * 37) % 101 for index in range(count)], 'a', 'b',
    )
    assert result['pair_count'] == 12
    assert sum(level['image_count'] for level in result['directions'][0]['levels']) == count
