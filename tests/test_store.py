from lingjing_harness.store import WorkspaceStore


def test_store_roundtrip(tmp_path):
    s=WorkspaceStore(tmp_path/"workspace.db")
    c=s.create_conversation()
    s.add_message(c["id"],"user","测试体验问题")
    s.add_message(c["id"],"assistant","完成",{"ok":True})
    loaded=s.get_conversation(c["id"])
    assert loaded["title"].startswith("测试体验问题")
    assert loaded["messages"][-1]["payload"]["ok"] is True
