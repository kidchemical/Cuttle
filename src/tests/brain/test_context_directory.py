"""The fresh envelope is a directory: names and pointers, not copied bodies."""
from api.cuttle_brain import context_compiler as cc


def test_runbooks_named_in_the_core_contract_are_not_listed_twice(tmp_path):
    project = tmp_path / "guest"
    (project / ".cuttle" / "docs").mkdir(parents=True)
    env = cc.compile_context("hello", project_path=str(project)).envelope
    assert env.count("action-forms.md   —") == 1  # described once, in the directory
    runtime = env[env.index("## Runtime context"):]
    more = next(line for line in runtime.splitlines() if "global docs (Cuttle)" in line)
    listed = more.split(" — ")[0]
    assert "action-forms.md" not in listed and "subagents.md" not in listed
    assert "git.md" in listed  # undescribed runbooks stay discoverable
    assert ".cuttle_global" in more  # with the folder that holds all of them

    # No core contract in this compile → nothing was described, so list everything.
    bare = cc.compile_context("hello", project_path=str(project), inject_capabilities=False).envelope
    assert "action-forms.md" in bare


def test_skill_summaries_are_clipped_to_a_trigger_line():
    long = "Create and maintain commands. " + "More detail about every option. " * 30
    clipped = cc._clip_summary(long)
    assert len(clipped) <= cc._SKILL_SUMMARY_CHARS + 1
    assert clipped.startswith("Create and maintain commands.")
    assert cc._clip_summary("Short one.") == "Short one."
