"""Pure hunk core: compute / keep / undo / fold (no I/O, no Cuttle imports)."""

from api.agent_review import hunks as H

BASE = "a\nb\nc\nd\ne\nf\ng\n"
# Two agent hunks: b→B, f deleted; plus an insertion after d.
AGENT = "a\nB\nc\nd\nnew\ne\ng\n"


def ids(base, cur):
    return [h["id"] for h in H.compute(base, cur)]


def test_compute_reports_line_ranges():
    hunks = H.compute(BASE, AGENT)
    assert [(h["old_start"], h["old_count"], h["new_start"], h["new_count"]) for h in hunks] == [
        (2, 1, 2, 1), (5, 0, 5, 1), (6, 1, 7, 0)]
    assert hunks[0]["removed"] == ["b\n"] and hunks[0]["added"] == ["B\n"]
    assert H.stats(BASE, AGENT) == {"hunks": 3, "additions": 2, "deletions": 2}
    assert H.compute(BASE, BASE) == []


def test_ids_stay_stable_while_other_hunks_are_kept():
    first, second, third = ids(BASE, AGENT)
    base2 = H.keep(BASE, AGENT, [first])
    assert ids(base2, AGENT) == [second, third]
    base3 = H.keep(base2, AGENT, [third])
    assert ids(base3, AGENT) == [second]
    assert H.keep(base3, AGENT, [second]) == AGENT


def test_undo_reverts_only_chosen_hunks():
    first, second, third = ids(BASE, AGENT)
    assert H.undo(BASE, AGENT, [second]) == "a\nB\nc\nd\ne\ng\n"
    assert H.undo(BASE, AGENT, [first, third]) == "a\nb\nc\nd\nnew\ne\nf\ng\n"
    assert H.undo(BASE, AGENT) == BASE and H.keep(BASE, AGENT) == AGENT


def test_created_and_deleted_files():
    created = H.compute(None, "x\ny\n")
    assert len(created) == 1 and created[0]["old_count"] == 0
    assert H.undo(None, "x\ny\n", [created[0]["id"]]) is None  # file goes away
    assert H.keep(None, "x\ny\n", []) is None
    deleted = H.compute("x\n", None)
    assert H.undo("x\n", None, [deleted[0]["id"]]) == "x\n"
    assert H.keep("x\n", None, [deleted[0]["id"]]) is None


def test_crlf_and_missing_final_newline_round_trip():
    base, cur = "a\r\nb\r\nc", "a\r\nB\r\nc"
    assert H.undo(base, cur) == base
    hid = ids(base, cur)[0]
    assert H.undo(base, cur, [hid]) == base and H.keep(base, cur, [hid]) == cur


def test_fold_moves_user_edits_outside_agent_hunks_into_baseline():
    user = "USER\n" + AGENT  # a line added at the top, far from agent hunks
    user = user.replace("g\n", "g!\n")  # edit next to nothing the agent touched
    base, conflicts = H.fold(BASE, AGENT, user)
    assert conflicts == 0
    assert base == "USER\na\nb\nc\nd\ne\nf\ng!\n"
    # Remaining hunks are exactly the agent's.
    assert ids(base, user) == ids(BASE, AGENT)


def test_fold_leaves_edits_inside_agent_hunks_in_the_hunk():
    user = AGENT.replace("B\n", "BB\n")
    base, conflicts = H.fold(BASE, AGENT, user)
    assert conflicts == 1 and base == BASE


def test_fold_is_a_no_op_without_user_changes():
    assert H.fold(BASE, AGENT, AGENT) == (BASE, 0)
    assert H.fold(None, "x\n", None) == (None, 1)


def test_large_repetitive_file_with_one_edit_diffs_fast():
    # Unity scenes are tens of thousands of near-identical YAML lines; an
    # untrimmed SequenceMatcher took seconds here and starved Flask's GIL.
    import time
    block = "--- !u!4 &1\nTransform:\n  m_Father: {fileID: 0}\n  m_Children: []\n"
    base = block * 15000
    cur = base[:len(base) // 2] + "  m_Name: Agent\n" + base[len(base) // 2:]
    started = time.perf_counter()
    hunks = H.compute(base, cur)
    assert time.perf_counter() - started < 1.0
    assert [(h["old_count"], h["new_count"], h["added"]) for h in hunks] == [(0, 1, ["  m_Name: Agent\n"])]
    assert H.undo(base, cur, [hunks[0]["id"]]) == base
    assert H.keep(base, cur, [hunks[0]["id"]]) == cur
