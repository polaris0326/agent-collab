"""验证文件保护、可移动性与实际 CLI 行为；不依赖第三方测试框架。"""

import hashlib
import contextlib
import importlib.util
import io
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest import mock
import zipfile

PACKAGE = Path(__file__).absolute().parents[1]
SCRIPT = PACKAGE / "agent_collab.py"
spec = importlib.util.spec_from_file_location("agent_collab", PACKAGE / "skills/agent-collab/scripts/agent_collab.py")
app = importlib.util.module_from_spec(spec)
spec.loader.exec_module(app)


def fingerprint(root):
    if not root.exists():
        return {}
    return {
        str(p.relative_to(root)): ("dir" if p.is_dir() else hashlib.sha256(p.read_bytes()).hexdigest())
        for p in root.rglob("*")
    }


class InitTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="agent-collab-")
        self.base = Path(self.temp.name)
        self.root = self.base / "中文 空格项目"

    def tearDown(self):
        self.temp.cleanup()

    def run_cli(self, *args, script=SCRIPT, env=None, cwd=None):
        return subprocess.run(
            [sys.executable, "-B", "-X", "utf8", str(script), *map(str, args)],
            cwd=cwd or self.base, capture_output=True, text=True, encoding="utf-8", env=env,
        )

    def initialize(self, *args):
        return self.run_cli("init", self.root, "--name", "中文项目", *args)

    def assert_ok(self, result):
        self.assertEqual(result.returncode, 0, result.stderr)

    def legacy_fixture(self):
        """纯文件 0.3.0 格式 fixture；不是旧宿主身份或 API 核验。"""
        self.root.mkdir()
        old_block = app.BEGIN + b"\nlegacy managed instructions\n" + app.END + b"\n"
        files = {
            app.META: app.json_bytes({"schema_version": 1, "agent_collab_version": "0.3.0", "name": "旧项目", "goal": "原目标", "task_state": app.STATE}),
            app.SKILL: b"legacy skill fixture\n",
            app.SKILL_SCRIPT: b"# legacy script fixture\n",
            app.STATE: "# 用户任务\nfixture 历史 P001 是隐藏身份，非真实宿主核验。\n".encode(),
            "AGENTS.md": b"\xef\xbb\xbfuser rules\r\n" + old_block + b"\r\nuser footer\r\n",
        }
        files[app.RECEIPT] = app.json_bytes({"schema_version": 1, "version": "0.3.0",
            "managed_sha256": {rel: app.sha(files[rel]) for rel in (app.META, app.SKILL, app.SKILL_SCRIPT)},
            "agents_block_sha256": app.sha(old_block), "mutable": [app.STATE]})
        for rel, data in files.items():
            target = self.root / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(data)
        return files

    def test_explicit_upgrade_backs_up_and_preserves_tasks_rules_then_is_idempotent(self):
        original = self.legacy_fixture()
        before = fingerprint(self.root)
        self.assertEqual(self.run_cli("init", self.root).returncode, 2)
        self.assert_ok(self.run_cli("upgrade", self.root, "--dry-run"))
        self.assertEqual(self.run_cli("upgrade", self.root, "--check").returncode, 1)
        self.assertEqual(before, fingerprint(self.root))
        self.assert_ok(self.run_cli("upgrade", self.root))
        for rel, data in original.items():
            self.assertEqual((self.root / ".agent-collab/backups/0.3.0" / rel).read_bytes(), data)
        self.assertEqual((self.root / app.STATE).read_bytes(), original[app.STATE])
        rules = (self.root / "AGENTS.md").read_bytes()
        self.assertTrue(rules.startswith(b"\xef\xbb\xbfuser rules\r\n"))
        self.assertTrue(rules.endswith(b"\r\nuser footer\r\n"))
        meta = json.loads((self.root / app.META).read_bytes())
        self.assertEqual((meta["name"], meta["goal"]), ("旧项目", "原目标"))
        self.assertEqual(meta["runtime"]["roles"], [])
        self.assertIsNotNone(meta["runtime"]["legacy_identity_source"])
        after = fingerprint(self.root)
        times = {p: p.stat().st_mtime_ns for p in self.root.rglob("*")}
        self.assert_ok(self.run_cli("upgrade", self.root))
        self.assert_ok(self.run_cli("init", self.root, "--check"))
        self.assertEqual(after, fingerprint(self.root))
        self.assertEqual(times, {p: p.stat().st_mtime_ns for p in self.root.rglob("*")})
        self.assertEqual(self.run_cli("role-reserve", self.root, "P", "--key", "initialization").returncode, 2)
        self.assert_ok(self.run_cli("role-reserve", self.root, "P", "--key", "initialization", "--after", "1"))
        meta = json.loads((self.root / app.META).read_bytes())
        self.assertEqual(meta["runtime"]["roles"][0]["id"], "P002")

    def test_upgrade_conflicts_leave_every_file_unchanged(self):
        for rel in (app.META, app.SKILL, app.SKILL_SCRIPT, app.RECEIPT, "AGENTS.md", "backup"):
            with self.subTest(rel=rel):
                self.root = self.base / ("upgrade-" + str(len(list(self.base.iterdir()))))
                self.legacy_fixture()
                if rel == "backup":
                    target = self.root / ".agent-collab/backups/0.3.0" / app.SKILL_SCRIPT
                    target.parent.mkdir(parents=True)
                    target.write_bytes(b"user backup")
                else:
                    target = self.root / rel
                    data = target.read_bytes()
                    if rel == app.META:
                        data = data.replace("旧项目".encode(), "改过名称".encode())
                    elif rel == "AGENTS.md":
                        data = data.replace(b"legacy managed", b"user changed")
                    else:
                        data += b"changed"
                    target.write_bytes(data)
                before = fingerprint(self.root)
                self.assertEqual(self.run_cli("upgrade", self.root).returncode, 2)
                self.assertEqual(before, fingerprint(self.root))

    def test_unknown_version_and_current_parameter_edits_are_not_adopted(self):
        self.legacy_fixture()
        target = self.root / app.META
        target.write_bytes(target.read_bytes().replace(b"0.3.0", b"9.0.0"))
        before = fingerprint(self.root)
        self.assertEqual(self.run_cli("upgrade", self.root).returncode, 2)
        self.assertEqual(before, fingerprint(self.root))
        self.root = self.base / "current"
        self.assert_ok(self.initialize())
        target = self.root / app.META
        metadata = json.loads(target.read_bytes())
        metadata["name"] = "unapproved change"
        target.write_bytes(app.json_bytes(metadata))
        before = fingerprint(self.root)
        self.assertEqual(self.run_cli("init", self.root).returncode, 2)
        self.assertEqual(self.run_cli("role-reserve", self.root, "P", "--key", "init").returncode, 2)
        self.assertEqual(before, fingerprint(self.root))

    def test_pending_role_reuse_client_id_is_not_ready_and_state_survives_init(self):
        self.assert_ok(self.initialize())
        self.assert_ok(self.run_cli("role-reserve", self.root, "P", "--key", "initialization"))
        self.assert_ok(self.run_cli("role-record", self.root, "P001", "--status", "creating"))
        self.assert_ok(self.run_cli("role-record", self.root, "P001", "--status", "pending", "--client-thread-id", "fixture-client-only"))
        before = fingerprint(self.root)
        self.assert_ok(self.run_cli("role-reserve", self.root, "P", "--key", "initialization"))
        self.assertEqual(self.run_cli("role-reserve", self.root, "P", "--key", "retry").returncode, 2)
        self.assertEqual(self.run_cli("role-record", self.root, "P001", "--status", "ready").returncode, 2)
        self.assertEqual(self.run_cli("role-record", self.root, "P001", "--status", "reserved").returncode, 2)
        self.assert_ok(self.initialize())
        self.assertEqual(before, fingerprint(self.root))
        self.assert_ok(self.run_cli("role-record", self.root, "P001", "--status", "unknown"))
        role = json.loads((self.root / app.META).read_bytes())["runtime"]["roles"][0]
        self.assertEqual(role["client_thread_id"], "fixture-client-only")
        self.assertIsNone(role["thread_id"])

    def test_synthetic_role_records_numbering_retirement_and_identity_immutability(self):
        # 以下全部是虚构字符串，只测试存储与冲突，不代表任何真实聊天或宿主核验。
        self.assert_ok(self.initialize())
        for kind in ("E", "P", "T", "R"):
            self.assert_ok(self.run_cli("role-reserve", self.root, kind, "--key", "fixture-only"))
        self.assert_ok(self.run_cli("role-record", self.root, "P001", "--status", "ready",
            "--thread-id", "fixture-thread", "--host-id", "fixture-host", "--project-id", "fixture-project",
            "--cwd", str(self.root), "--evidence", "synthetic storage test; no host verification"))
        before = fingerprint(self.root)
        self.assertEqual(self.run_cli("role-record", self.root, "P001", "--status", "ready", "--thread-id", "different-fixture").returncode, 2)
        self.assertEqual(before, fingerprint(self.root))
        self.assert_ok(self.run_cli("role-record", self.root, "P001", "--status", "retired"))
        self.assertEqual(self.run_cli("role-record", self.root, "P001", "--status", "ready").returncode, 2)
        self.assert_ok(self.run_cli("role-reserve", self.root, "P", "--key", "fixture-replacement"))
        self.assert_ok(self.run_cli("role-reserve", self.root, "T", "--key", "fixture-next-task"))
        self.assert_ok(self.run_cli("role-record", self.root, "T002", "--status", "reserved", "--title", "T002｜文件迁移"))
        metadata = json.loads((self.root / app.META).read_bytes())
        self.assertEqual([r["id"] for r in metadata["runtime"]["roles"]], ["E001", "P001", "T001", "R001", "P002", "T002"])
        self.assertEqual(metadata["runtime"]["roles"][-1]["title"], "T002｜文件迁移")
        before = fingerprint(self.root)
        self.assert_ok(self.initialize())
        self.assertEqual(before, fingerprint(self.root))
        metadata["runtime"]["next_number"]["P"] = 1
        (self.root / app.META).write_bytes(app.json_bytes(metadata))
        before = fingerprint(self.root)
        self.assertEqual(self.run_cli("init", self.root).returncode, 2)
        self.assertEqual(before, fingerprint(self.root))

    def test_authorization_record_needs_project_and_paired_actual_scope(self):
        # 只测试虚构授权文本的本地保存，不代表真人同意或宿主授予权限。
        self.assert_ok(self.initialize())
        self.assert_ok(self.run_cli("role-reserve", self.root, "P", "--key", "fixture"))
        before = fingerprint(self.root)
        base = ("role-record", self.root, "P001", "--status", "reserved")
        source = ("--authorization-source", "synthetic fixture reference")
        scope = ("--authorization-scope", "synthetic scope; no actual permission")
        self.assertEqual(self.run_cli(*base, *source).returncode, 2)
        self.assertEqual(self.run_cli(*base, *source, *scope).returncode, 2)
        self.assertEqual(before, fingerprint(self.root))
        self.assert_ok(self.run_cli(*base, "--project-id", "fixture-project", *source, *scope))
        recorded = fingerprint(self.root)
        self.assert_ok(self.initialize())
        self.assertEqual(recorded, fingerprint(self.root))
        self.assert_ok(self.run_cli(*base, "--authorization-source", "synthetic later revocation", "--authorization-scope", "revoked fixture scope"))
        role = json.loads((self.root / app.META).read_bytes())["runtime"]["roles"][0]
        self.assertEqual(role["authorization_scope"], "revoked fixture scope")
        self.assertEqual(role["project_id"], "fixture-project")

    def test_new_directory_and_nondefault_cwd(self):
        self.assert_ok(self.initialize("--goal", "形成可验证的成果"))
        self.assertEqual(set(fingerprint(self.root)), {
            "AGENTS.md", ".agent-collab", ".agents", str(Path(".agents/skills")),
            str(Path(".agents/skills/agent-collab")),
            str(Path(".agents/skills/agent-collab/scripts")),
            *map(lambda p: str(Path(p)), [app.META, app.SKILL, app.SKILL_SCRIPT, app.STATE, app.RECEIPT]),
        })
        metadata = json.loads((self.root / app.META).read_text(encoding="utf-8"))
        self.assertEqual(metadata["goal"], "形成可验证的成果")
        self.assertFalse((self.root / ".git").exists())

    def test_dry_run_and_check_do_not_create_directory(self):
        self.assert_ok(self.initialize("--dry-run"))
        self.assertFalse(self.root.exists())
        self.assertEqual(self.initialize("--check").returncode, 1)
        self.assertFalse(self.root.exists())

    def test_existing_agents_bytes_and_user_files_preserved(self):
        self.root.mkdir()
        original = b"\xef\xbb\xbf# Existing rules\r\nDo not rewrite."
        (self.root / "AGENTS.md").write_bytes(original)
        (self.root / "user.bin").write_bytes(b"\x00\xfforiginal")
        self.assert_ok(self.initialize())
        self.assertTrue((self.root / "AGENTS.md").read_bytes().startswith(original + b"\n\n"))
        self.assertEqual((self.root / "user.bin").read_bytes(), b"\x00\xfforiginal")

    def test_idempotence_including_mtime(self):
        self.assert_ok(self.initialize())
        before = fingerprint(self.root)
        times = {p: p.stat().st_mtime_ns for p in self.root.rglob("*")}
        self.assert_ok(self.initialize())
        self.assert_ok(self.initialize("--check"))
        self.assertEqual(before, fingerprint(self.root))
        self.assertEqual(times, {p: p.stat().st_mtime_ns for p in self.root.rglob("*")})

    def test_daily_state_and_outside_agents_edits_survive(self):
        self.assert_ok(self.initialize())
        (self.root / app.STATE).write_text("# 当前任务\n执行中：用户正在编辑\n", encoding="utf-8")
        agents = self.root / "AGENTS.md"
        agents.write_bytes(b"# Added before\n" + agents.read_bytes() + b"\n# Added after\n")
        before = fingerprint(self.root)
        self.assert_ok(self.initialize())
        self.assertEqual(before, fingerprint(self.root))

    def test_all_managed_edits_stop_without_any_changes(self):
        for rel in (app.META, app.SKILL, app.SKILL_SCRIPT, app.RECEIPT, "AGENTS.md"):
            with self.subTest(rel=rel):
                self.root = self.base / ("case-" + str(len(list(self.base.iterdir()))))
                self.assert_ok(self.initialize())
                target = self.root / rel
                data = target.read_bytes()
                if rel == "AGENTS.md":
                    data = data.replace(app.BEGIN, app.BEGIN + b" changed")
                else:
                    data += b"\nuser change\n"
                target.write_bytes(data)
                before = fingerprint(self.root)
                self.assertEqual(self.initialize().returncode, 2)
                self.assertEqual(before, fingerprint(self.root))

    def test_late_conflict_does_not_append_agents_or_create_early_files(self):
        target = self.root / app.SKILL
        target.parent.mkdir(parents=True)
        target.write_text("user-owned skill", encoding="utf-8")
        (self.root / "AGENTS.md").write_bytes(b"user rules")
        before = fingerprint(self.root)
        self.assertEqual(self.initialize().returncode, 2)
        self.assertEqual(before, fingerprint(self.root))

    def test_different_parameters_and_missing_state_conflict(self):
        self.assert_ok(self.initialize())
        before = fingerprint(self.root)
        self.assertEqual(self.initialize("--goal", "new goal").returncode, 2)
        self.assertEqual(before, fingerprint(self.root))
        (self.root / app.STATE).unlink()
        before = fingerprint(self.root)
        self.assertEqual(self.initialize().returncode, 2)
        self.assertEqual(before, fingerprint(self.root))

    def test_install_does_not_initialize_and_preserves_existing_rules(self):
        self.root.mkdir()
        rules = self.root / "AGENTS.md"
        rules.write_bytes(b"existing rules\r\n")
        before = fingerprint(self.root)
        self.assert_ok(self.run_cli("install", self.root, "--dry-run"))
        self.assertEqual(before, fingerprint(self.root))
        self.assertEqual(self.run_cli("install", self.root, "--check").returncode, 1)
        self.assert_ok(self.run_cli("install", self.root))
        self.assertFalse((self.root / app.META).exists())
        self.assertFalse((self.root / app.STATE).exists())
        self.assertEqual(rules.read_bytes(), b"existing rules\r\n")
        installed = fingerprint(self.root)
        self.assert_ok(self.run_cli("install", self.root))
        self.assert_ok(self.run_cli("install", self.root, "--check"))
        self.assertEqual(installed, fingerprint(self.root))

    def test_complete_skill_alone_initializes_with_defaults_and_reuses_metadata(self):
        detached = self.base / "脱离下载包的 Skill"
        shutil.copytree(PACKAGE / "skills/agent-collab", detached)
        script = detached / "scripts/agent_collab.py"
        self.assert_ok(self.run_cli("install", self.root, script=script))
        installed_script = self.root / app.SKILL_SCRIPT
        self.assert_ok(self.run_cli("init", script=installed_script, cwd=self.root))
        meta = json.loads((self.root / app.META).read_text(encoding="utf-8"))
        self.assertEqual(meta["name"], self.root.name)
        self.assertIsNone(meta["goal"])
        state = self.root / app.STATE
        state.write_text("# P 已就绪\n实际路由保存在此；日常任务已更新\n", encoding="utf-8")
        before = fingerprint(self.root)
        self.assert_ok(self.run_cli("init", script=installed_script, cwd=self.root))
        self.assertEqual(before, fingerprint(self.root))
        moved = self.base / "移动后目录名不同"
        self.root.rename(moved)
        self.assert_ok(self.run_cli("init", "--check", script=moved / app.SKILL_SCRIPT, cwd=moved))
        self.assertEqual(json.loads((moved / app.META).read_text(encoding="utf-8"))["name"], meta["name"])

    def test_install_conflict_and_legacy_project_remain_unchanged(self):
        installed = self.root / app.SKILL
        installed.parent.mkdir(parents=True)
        installed.write_text("user customized skill", encoding="utf-8")
        before = fingerprint(self.root)
        self.assertEqual(self.run_cli("install", self.root).returncode, 2)
        self.assertEqual(before, fingerprint(self.root))
        legacy = self.base / "旧项目"
        (legacy / ".agent-collab").mkdir(parents=True)
        (legacy / app.META).write_text(json.dumps({"agent_collab_version": "0.1.0", "name": "旧名称"}), encoding="utf-8")
        before = fingerprint(legacy)
        for command in ("install", "init"):
            self.assertEqual(self.run_cli(command, legacy).returncode, 2)
            self.assertEqual(before, fingerprint(legacy))

    def test_implicit_parameters_keep_existing_goal(self):
        self.assert_ok(self.initialize("--goal", "已存目标"))
        before = fingerprint(self.root)
        self.assert_ok(self.run_cli("init", self.root))
        self.assertEqual(before, fingerprint(self.root))

    def test_direct_source_init_without_prior_install(self):
        self.root.mkdir()
        self.assertEqual(list(self.root.iterdir()), [])
        self.assert_ok(self.run_cli("init", self.root))
        metadata = json.loads((self.root / app.META).read_text(encoding="utf-8"))
        self.assertEqual(metadata["name"], self.root.name)
        self.assertIsNone(metadata["goal"])
        for relative in app.SKILL_FILES:
            self.assertEqual(
                (self.root / ".agents/skills/agent-collab" / relative).read_bytes(),
                (PACKAGE / "skills/agent-collab" / relative).read_bytes(),
            )
        for source_file in ("README.md", "INITIALIZE.md", "agent_collab.py", "tests"):
            self.assertFalse((self.root / source_file).exists())
        state = self.root / app.STATE
        state.write_text("# 日常状态\n保留用户后续记录。\n", encoding="utf-8")
        before = fingerprint(self.root)
        self.assert_ok(self.run_cli("init", self.root))
        self.assertEqual(before, fingerprint(self.root))
        self.assert_ok(self.run_cli("init", "--check", script=self.root / app.SKILL_SCRIPT, cwd=self.root))

    def test_external_installed_skill_uses_project_cwd_without_modifying_install(self):
        installed = self.base / "模拟用户 skills" / "agent-collab"
        shutil.copytree(PACKAGE / "skills/agent-collab", installed)
        script = installed / "scripts/agent_collab.py"
        source_before = fingerprint(installed)
        source_times = {p: p.stat().st_mtime_ns for p in installed.rglob("*")}
        self.root.mkdir()
        self.assertEqual(list(self.root.iterdir()), [])
        self.assert_ok(self.run_cli("init", script=script, cwd=self.root))
        meta = json.loads((self.root / app.META).read_text(encoding="utf-8"))
        self.assertEqual(meta["name"], self.root.name)
        self.assertIsNone(meta["goal"])
        state = self.root / app.STATE
        state.write_text(state.read_text(encoding="utf-8") + "\n日常状态保持。\n", encoding="utf-8")
        before_repeat = fingerprint(self.root)
        self.assert_ok(self.run_cli("init", script=script, cwd=self.root))
        self.assertEqual(before_repeat, fingerprint(self.root))
        existing = self.base / "已有中文项目"
        existing.mkdir()
        original = b"Existing rules\r\n"
        (existing / "AGENTS.md").write_bytes(original)
        (existing / "user.txt").write_bytes(b"keep")
        self.assert_ok(self.run_cli("init", existing, script=script, cwd=existing))
        self.assertTrue((existing / "AGENTS.md").read_bytes().startswith(original))
        self.assertEqual((existing / "user.txt").read_bytes(), b"keep")
        self.assertEqual(source_before, fingerprint(installed))
        self.assertEqual(source_times, {p: p.stat().st_mtime_ns for p in installed.rglob("*")})
        self.assertFalse((installed / ".agent-collab").exists())

    def test_invalid_markers_and_non_utf8_agents_are_preserved(self):
        for data in (app.BEGIN, app.END + app.BEGIN, b"\xff\xfeinvalid"):
            self.root = self.base / ("case-" + str(len(list(self.base.iterdir()))))
            self.root.mkdir()
            (self.root / "AGENTS.md").write_bytes(data)
            before = fingerprint(self.root)
            self.assertEqual(self.initialize().returncode, 2)
            self.assertEqual(before, fingerprint(self.root))

    def test_path_escape_and_directory_collision(self):
        result = self.run_cli("init", str(self.base / "inside" / ".." / "escape"), "--name", "x")
        self.assertEqual(result.returncode, 2)
        self.assertFalse((self.base / "escape").exists())
        (self.root / "AGENTS.md").mkdir(parents=True)
        before = fingerprint(self.root)
        self.assertEqual(self.initialize().returncode, 2)
        self.assertEqual(before, fingerprint(self.root))

    def test_symlink_target_is_rejected(self):
        outside = self.base / "outside"
        outside.mkdir()
        try:
            self.root.symlink_to(outside, target_is_directory=True)
        except OSError as exc:
            self.skipTest(f"当前环境无法创建符号链接：{exc}")
        self.assertEqual(self.initialize().returncode, 2)
        self.assertEqual(list(outside.iterdir()), [])

    def test_hardlinked_agents_is_rejected(self):
        self.root.mkdir()
        outside = self.base / "outside.txt"
        outside.write_bytes(b"keep")
        try:
            os.link(outside, self.root / "AGENTS.md")
        except OSError as exc:
            self.skipTest(f"当前文件系统无法创建硬链接：{exc}")
        self.assertEqual(self.initialize().returncode, 2)
        self.assertEqual(outside.read_bytes(), b"keep")
        self.assertFalse((self.root / app.META).exists())

    @unittest.skipUnless(os.name == "nt", "Windows 专用的目录联接测试")
    def test_windows_junction_component_is_rejected(self):
        outside = self.base / "outside-junction"
        outside.mkdir()
        self.root.mkdir()
        junction = self.root / ".agents"
        result = subprocess.run(["cmd", "/c", "mklink", "/J", str(junction), str(outside)], capture_output=True)
        if result.returncode:
            self.skipTest("当前环境无法创建 Windows 目录联接")
        try:
            self.assertEqual(self.initialize().returncode, 2)
            self.assertEqual(list(outside.iterdir()), [])
            self.assertFalse((self.root / "AGENTS.md").exists())
        finally:
            # 只移除已知联接本身，不递归处理指向目录。
            os.rmdir(junction)

    def test_move_project_then_check(self):
        self.assert_ok(self.initialize())
        moved = self.base / "搬迁后的项目"
        self.root.rename(moved)
        self.root = moved
        self.assert_ok(self.initialize("--check"))
        data = (self.root / "AGENTS.md").read_text(encoding="utf-8")
        for rel in (app.META, app.STATE, app.SKILL):
            self.assertIn("(" + rel + ")", data)
            self.assertTrue((self.root / rel).is_file())

    @unittest.skipUnless(shutil.which("git"), "未安装 Git")
    def test_git_is_explicit_and_check_is_read_only(self):
        self.assert_ok(self.initialize("--git", "--dry-run"))
        self.assertFalse(self.root.exists())
        self.assert_ok(self.initialize("--git"))
        self.assertTrue((self.root / ".git").is_dir())
        before = fingerprint(self.root)
        self.assert_ok(self.initialize("--git", "--check"))
        self.assertEqual(before, fingerprint(self.root))
        child = self.root / "child"
        self.assert_ok(self.run_cli("init", child, "--name", "child", "--git"))
        self.assertFalse((child / ".git").exists())

    @unittest.skipUnless(shutil.which("git"), "未安装 Git")
    def test_git_routing_environment_rejected_before_writes(self):
        other = self.base / "other-repository"
        self.assert_ok(self.run_cli("init", other, "--name", "other", "--git"))
        before = fingerprint(other)
        environment = os.environ.copy()
        environment.update(GIT_DIR=str(other / ".git"), GIT_WORK_TREE=str(other))
        args = ("init", self.root, "--name", "target")
        for mode in ((), ("--dry-run",), ("--check",)):
            result = self.run_cli(*args, "--git", *mode, env=environment)
            self.assertEqual(result.returncode, 2, result.stderr)
            self.assertFalse(self.root.exists())
            self.assertEqual(before, fingerprint(other))
        # 未请求 Git 的普通初始化不依赖或修改这些变量，也不触碰另一仓库。
        self.assert_ok(self.run_cli(*args, env=environment))
        self.assertFalse((self.root / ".git").exists())
        self.assertEqual(before, fingerprint(other))

    @unittest.skipUnless(shutil.which("git"), "未安装 Git")
    def test_git_zero_exit_without_target_repository_is_not_success(self):
        fake_success = subprocess.CompletedProcess([], 0, stdout="", stderr="")
        with mock.patch.object(app.subprocess, "run", return_value=fake_success):
            with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
                result = app.main(["init", str(self.root), "--name", "target", "--git"])
        self.assertEqual(result, 3)
        self.assertTrue((self.root / "AGENTS.md").is_file())
        self.assertFalse((self.root / ".git").exists())

    def test_distribution_whitelist_hashes_and_extracted_use(self):
        copied = self.base / "source"
        for rel in app.PUBLIC_FILES:
            target = copied / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(PACKAGE / rel, target)
        (copied / "not-for-release.txt").write_text("excluded", encoding="utf-8")
        archive = self.base / "release.zip"
        second = self.base / "release2.zip"
        self.assert_ok(self.run_cli("pack", "--output", archive, script=copied / "agent_collab.py"))
        self.assert_ok(self.run_cli("pack", "--output", second, script=copied / "agent_collab.py"))
        self.assertEqual(archive.read_bytes(), second.read_bytes())
        self.assertEqual(self.run_cli("pack", "--output", archive, script=copied / "agent_collab.py").returncode, 2)
        extracted = self.base / "outside-source"
        with zipfile.ZipFile(archive) as packed:
            prefix = "agent-collab-" + app.VERSION + "/"
            self.assertEqual(set(packed.namelist()), {prefix + f for f in (*app.PUBLIC_FILES, "MANIFEST.json")})
            manifest = json.loads(packed.read(prefix + "MANIFEST.json"))
            for rel, digest in manifest["files"].items():
                self.assertEqual(hashlib.sha256(packed.read(prefix + rel)).hexdigest(), digest)
            packed.extractall(extracted)
        expected = hashlib.sha256(archive.read_bytes()).hexdigest()
        self.assertTrue(archive.with_name("release.zip.sha256").read_text().startswith(expected))
        script = extracted / ("agent-collab-" + app.VERSION) / "agent_collab.py"
        self.assert_ok(self.run_cli("init", self.root, "--name", "解压后项目", script=script))
        self.assert_ok(self.run_cli("init", self.root, "--name", "解压后项目", "--check", script=script))


if __name__ == "__main__":
    unittest.main()
