"""Fast deterministic smoke for the offline match batch CLI (AND-71 offline)."""

import importlib.util
import json
import sys
from pathlib import Path

BATCH_SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "validate_match_batches.py"


def load_batch_module():  # type: ignore[no-untyped-def]
    spec = importlib.util.spec_from_file_location("validate_match_batches", BATCH_SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules["validate_match_batches"] = module
    spec.loader.exec_module(module)
    return module


BATCH = load_batch_module()


def smoke_specs():  # type: ignore[no-untyped-def]
    return [BATCH.SCENARIOS[name] for name in ("baseline", "scripted_commands")]


def test_batch_smoke_passes_and_replays_cleanly() -> None:
    content = BATCH.run_batch([7], smoke_specs())
    assert content["failed_assertions"] == 0
    assert content["metadata"]["engine_version"] == "regulation-v2"
    assert content["metadata"]["policy_version"] == "match-policy-v2"
    assert content["metadata"]["replay_mismatches"] == 0
    assert content["metadata"]["checkpoint_mismatches"] == 0
    assert content["metadata"]["checkpoint_total"] == 2 * len(BATCH.CHECKPOINT_TICKS)
    for row in content["matches"]:
        assert row["phase"] == "full_time"
        assert row["tick"] == 30
        assert row["replay_ok"] is True


def test_batch_content_is_deterministic_for_same_seed() -> None:
    first = BATCH.run_batch([7], smoke_specs())
    second = BATCH.run_batch([7], smoke_specs())
    assert BATCH.content_hash(first) == BATCH.content_hash(second)


def test_short_squad_and_last_season_snapshot_rules() -> None:
    specs = [BATCH.SCENARIOS[name] for name in ("short_squad_11", "last_season_starter")]
    content = BATCH.run_batch([7], specs)
    assert content["failed_assertions"] == 0
    short = next(r for r in content["matches"] if r["scenario"] == "short_squad_11")
    assert short["home_participants"] == 11
    assert short["phase"] == "full_time"


def test_abandoned_is_a_separate_outcome_without_30_ticks() -> None:
    content = BATCH.run_batch([11], [BATCH.SCENARIOS["dismissal_red_storm"]])
    assert content["failed_assertions"] == 0
    assert content["matches"]
    for row in content["matches"]:
        assert row["phase"] == "abandoned"
        assert row["tick"] < 30


def test_cli_smoke_writes_bounded_reports(tmp_path) -> None:  # type: ignore[no-untyped-def]
    output = tmp_path / "batch"
    code = BATCH.main(
        [
            "--seeds",
            "7,8",
            "--scenarios",
            "baseline,scripted_commands",
            "--output",
            str(output),
            "--csv",
        ]
    )
    assert code == 0
    report = json.loads(output.with_suffix(".json").read_text(encoding="utf-8"))
    assert report["content_hash"] == BATCH.content_hash(
        {k: v for k, v in report.items() if k != "content_hash"}
    )
    assert output.with_suffix(".md").stat().st_size > 0
    assert output.with_suffix(".csv").stat().st_size > 0
    # Same seeds give the same substantive report content.
    again = tmp_path / "batch-again"
    assert (
        BATCH.main(
            [
                "--seeds",
                "7,8",
                "--scenarios",
                "baseline,scripted_commands",
                "--output",
                str(again),
            ]
        )
        == 0
    )
    other = json.loads(again.with_suffix(".json").read_text(encoding="utf-8"))
    assert other["content_hash"] == report["content_hash"]


def test_cli_rejects_over_budget_batches() -> None:
    code = BATCH.main(["--seeds", "7,8", "--scenarios", "baseline,home_swap", "--max-matches", "1"])
    assert code == 2
