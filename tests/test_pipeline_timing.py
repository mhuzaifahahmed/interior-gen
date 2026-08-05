import time
from concurrent.futures import ThreadPoolExecutor

from app.pipeline.timing import PipelineTimer


def test_stage_records_a_duration():
    timer = PipelineTimer("run-1")
    with timer.stage("describe_room"):
        time.sleep(0.01)

    summary = timer.summary()
    assert summary["run_id"] == "run-1"
    assert len(summary["stages"]) == 1
    assert summary["stages"][0]["name"] == "describe_room"
    assert summary["stages"][0]["duration_s"] > 0


def test_repeated_stage_names_are_recorded_separately_not_averaged():
    timer = PipelineTimer("run-2")
    with timer.stage("generate_image:economical"):
        pass
    with timer.stage("generate_image:mid"):
        pass

    names = [s["name"] for s in timer.summary()["stages"]]
    assert names == ["generate_image:economical", "generate_image:mid"]


def test_total_duration_covers_the_whole_run_not_just_stages():
    timer = PipelineTimer("run-3")
    time.sleep(0.02)
    with timer.stage("a_stage"):
        pass

    summary = timer.summary()
    assert summary["total_duration_s"] >= summary["stages"][0]["duration_s"]


def test_stage_is_thread_safe_under_concurrent_use():
    # generate.py's image-generation stage runs one stage() per tier on a
    # worker thread concurrently - a plain (non-locked) list.append from
    # multiple threads risks a lost update / corrupted list.
    timer = PipelineTimer("run-4")

    def record(name):
        with timer.stage(name):
            time.sleep(0.01)

    with ThreadPoolExecutor(max_workers=5) as pool:
        list(pool.map(record, [f"tier-{i}" for i in range(20)]))

    assert len(timer.summary()["stages"]) == 20


def test_log_summary_returns_the_same_stages_as_summary():
    # Not a full-dict equality check against summary() - total_duration_s is
    # a live monotonic() sample, so two separate calls can legitimately
    # differ by a sub-millisecond amount and would make this test flaky.
    timer = PipelineTimer("run-5")
    with timer.stage("a_stage"):
        pass

    logged = timer.log_summary()
    assert logged["run_id"] == "run-5"
    assert logged["stages"] == timer.summary()["stages"]
