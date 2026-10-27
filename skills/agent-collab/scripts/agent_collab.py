#!/usr/bin/env python3
"""项目本地协作初始化与白名单打包；运行时仅依赖 Python 标准库。"""

from __future__ import annotations

import argparse
import hashlib
import io
import json
import os
from pathlib import Path
import shutil
import stat
import subprocess
import sys
import tempfile
import zipfile

VERSION = "0.4.0"
SKILL_SOURCE = Path(__file__).absolute().parents[1]
SOURCE = SKILL_SOURCE.parents[1]
BEGIN = b"<!-- agent-collab:begin -->"
END = b"<!-- agent-collab:end -->"
META = ".agent-collab/project.json"
STATE = ".agent-collab/tasks.md"
RECEIPT = ".agent-collab/manifest.json"
SKILL = ".agents/skills/agent-collab/SKILL.md"
SKILL_SCRIPT = ".agents/skills/agent-collab/scripts/agent_collab.py"
SKILL_FILES = ("SKILL.md", "scripts/agent_collab.py")
PUBLIC_FILES = (
    "README.md",
    "INITIALIZE.md",
    "VERSION",
    "agent_collab.py",
    "skills/agent-collab/SKILL.md",
    "skills/agent-collab/scripts/agent_collab.py",
    "tests/test_init.py",
)
GIT_ROUTING_ENV = {
    "GIT_DIR", "GIT_WORK_TREE", "GIT_COMMON_DIR", "GIT_INDEX_FILE",
    "GIT_OBJECT_DIRECTORY", "GIT_ALTERNATE_OBJECT_DIRECTORIES",
    "GIT_CONFIG", "GIT_CONFIG_COUNT", "GIT_CONFIG_PARAMETERS",
    "GIT_CONFIG_GLOBAL", "GIT_CONFIG_SYSTEM",
}
ROLE_NAMES = {"E": "协作环境", "P": "日常伙伴", "T": "任务执行", "R": "独立审核"}
ROLE_STATES = {"reserved", "creating", "pending", "unknown", "ready", "retired"}


class Conflict(Exception):
    """预检发现不能安全合并的输入。"""


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def json_bytes(value: object) -> bytes:
    return (json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode("utf-8")


def safe_path(path: Path) -> Path:
    """不解析链接；检查从盘根开始的每一个现有路径组件。"""
    if ".." in path.parts:
        raise Conflict(f"路径不能含父级跳转 '..'：{path}")
    absolute = path.absolute()
    for part in absolute.parts[1:]:
        if ":" in part or part.endswith((".", " ")):
            raise Conflict(f"路径包含不支持的别名或组件：{path}")
    for item in list(reversed(absolute.parents)) + [absolute]:
        try:
            info = item.lstat()
        except FileNotFoundError:
            continue
        if stat.S_ISLNK(info.st_mode) or getattr(info, "st_file_attributes", 0) & 0x400:
            raise Conflict(f"拒绝链接或重解析点：{item}")
        if item != absolute and not stat.S_ISDIR(info.st_mode):
            raise Conflict(f"路径父级不是目录：{item}")
        if stat.S_ISREG(info.st_mode) and info.st_nlink > 1:
            raise Conflict(f"拒绝多硬链接文件：{item}")
    return absolute


def read_optional(path: Path) -> bytes | None:
    safe_path(path)
    try:
        info = path.lstat()
    except FileNotFoundError:
        return None
    if not stat.S_ISREG(info.st_mode):
        raise Conflict(f"目标不是普通文件：{path}")
    data = path.read_bytes()
    try:
        data.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise Conflict(f"文件须为 UTF-8，未修改：{path}") from exc
    return data


def block() -> bytes:
    return BEGIN + """
## agent-collab 项目协作入口

- 项目信息：[project.json](.agent-collab/project.json)。唯一任务状态入口：[tasks.md](.agent-collab/tasks.md)。
- 本地 Skill：[agent-collab](.agents/skills/agent-collab/SKILL.md)，在本项目调用 `$agent-collab`。
- 可见入口：E001｜协作环境优先复用本项目发起聊天；P001｜日常伙伴为可见用户聊天。实际编号/路由与就绪事实以 project.json 的 runtime 为准，文件存在不代表角色就绪。
- E 负责环境与入口转交；P 唯一收口日常任务。小改 P 自做；T 按真实任务创建，重要改动由独立 R 审核。T/R 也使用明确获准的可见聊天，返修沿原 T、复核沿原 R。
- 编号按项目、按 E/P/T/R 各自递增且不回收。身份只记 project.json；tasks.md 仅用角色编号引用，不另存活跃路由。升级前的旧身份文字仅作历史，不能当可见就绪证据。
- 新聊天须有真人明确授权；Skill 不是授权。查证真实 threadId/host/project/cwd，不能用隐藏子 Agent、标题或 pending clientThreadId 替代。创建结果未知先查证，不重复创建。
- 本段只补充协作入口，保留已有规则。若与既有规则或当前授权冲突，先明确适用边界。
""".encode("utf-8") + END + b"\n"


def initial_state() -> bytes:
    return """# 任务状态

这里是本项目唯一任务状态入口，由当前 P 统一维护；日常编辑此文件不会被初始化器覆盖。
当前没有已委托的任务。初始化不自动执行项目目标。

角色身份与文件/聊天就绪的区别见 [project.json](project.json) 和本地 Skill。
这里不维护第二份角色路由；脚本不创建或核验 E/P/T/R。

## 当前任务

每项任务保留一条当前记录，至少包含：
- 目标、范围、成功标准、当前授权与限制；
- 唯一收口 P 的编号，按需引用 T/R 编号及当前任务职责；实际路由从项目元数据读取；
- 当前状态（待执行／执行中／待审核／返修中／待接受／完成／暂停或阻塞）；
- 固定候选（完整提交 SHA，或交付文件清单与 SHA256）、验证证据、审核结论所针对的候选；
- 下一行动人；等待时说明原因和解除条件；接受、合入、发布各自的事实。

若项目已经使用外部任务系统，P 可将本文件改成唯一规范入口的链接和使用规则，
将状态迁移到该处，不在两边复制维护。迁移本身需遵守当前写入权限。
""".encode("utf-8")


def validate_text(value: str, label: str, limit: int) -> str:
    value = value.strip()
    if not value or len(value) > limit or any(ord(c) < 32 for c in value):
        raise Conflict(f"{label}须为 1–{limit} 个字符的单行文本")
    return value


def skill_files() -> dict[str, bytes]:
    files = {}
    for rel in SKILL_FILES:
        data = read_optional(SKILL_SOURCE / rel)
        if data is None:
            raise Conflict(f"Skill 不完整，缺少 {rel}；请复制完整 agent-collab 文件夹")
        files[".agents/skills/agent-collab/" + rel] = data
    return files


def parse_object(data: bytes | None, label: str) -> dict:
    try:
        value = json.loads(data) if data is not None else None
    except (ValueError, UnicodeError) as exc:
        raise Conflict(label + "不是有效 JSON，未修改") from exc
    if not isinstance(value, dict):
        raise Conflict(label + "须为 JSON 对象")
    return value


def empty_runtime() -> dict:
    return {"next_number": {kind: 1 for kind in ROLE_NAMES}, "roles": [], "legacy_identity_source": None}


def validate_runtime(runtime: object) -> None:
    if not isinstance(runtime, dict) or set(runtime) != {"next_number", "roles", "legacy_identity_source"}:
        raise Conflict("runtime 结构无效；保留原文件，不能重置身份")
    counters, roles = runtime["next_number"], runtime["roles"]
    if not isinstance(counters, dict) or set(counters) != set(ROLE_NAMES) or not isinstance(roles, list):
        raise Conflict("角色编号或身份列表无效")
    if runtime["legacy_identity_source"] not in (None, "backups/0.3.0/.agent-collab/tasks.md"):
        raise Conflict("旧身份来源无效")
    ids, keys, routes, active = set(), set(), set(), set()
    maximum = dict.fromkeys(ROLE_NAMES, 0)
    fields = {"id", "kind", "key", "title", "status", "thread_id", "client_thread_id", "host_id", "project_id", "cwd", "evidence", "authorization_source", "authorization_scope"}
    for role in roles:
        if not isinstance(role, dict) or set(role) != fields:
            raise Conflict("角色字段无效")
        kind, ident, key = role["kind"], role["id"], role["key"]
        if not isinstance(kind, str) or kind not in ROLE_NAMES or not isinstance(ident, str) or not ident.startswith(kind):
            raise Conflict("角色编号无效")
        number = ident[1:]
        if not number.isascii() or not number.isdigit() or int(number) < 1 or ident != f"{kind}{int(number):03d}":
            raise Conflict("角色编号须为字母加至少三位递增数字")
        if not isinstance(key, str) or not key or ident in ids or (kind, key) in keys:
            raise Conflict("角色编号或请求 key 重复/无效")
        ids.add(ident)
        keys.add((kind, key))
        maximum[kind] = max(maximum[kind], int(number))
        if not isinstance(role["status"], str) or role["status"] not in ROLE_STATES:
            raise Conflict("未知角色状态")
        for field in fields - {"kind", "id", "key", "status"}:
            value = role[field]
            if value is not None and (not isinstance(value, str) or not value.strip() or any(ord(c) < 32 for c in value)):
                raise Conflict("角色字段须为非空单行文本或 null：" + field)
        if not isinstance(role["title"], str) or not role["title"].startswith(ident + "｜") or not role["title"][len(ident) + 1:].strip():
            raise Conflict("实际标题须保留角色编号和分隔符，例如 P001｜日常伙伴")
        if role["authorization_source"] is not None or role["authorization_scope"] is not None:
            if kind != "P" or not all(role[field] for field in ("authorization_source", "authorization_scope", "project_id")):
                raise Conflict("授权来源/范围须成对记录在已绑定 projectId 的 P 身份；记录不自行授予权限")
        if role["thread_id"] is not None:
            route = (role["host_id"], role["thread_id"])
            if route in routes:
                raise Conflict("同一真实聊天不能分配给第二个角色编号；保留旧身份")
            routes.add(route)
        if kind in ("E", "P") and role["status"] != "retired":
            if kind in active:
                raise Conflict("每项目只能有一个未退役的 " + kind)
            active.add(kind)
        if role["status"] == "ready" and any(not role[field] for field in ("thread_id", "host_id", "project_id", "cwd", "evidence")):
            raise Conflict("ready 需要真实 thread/host/project/cwd 和宿主回读证据；clientThreadId 不算 threadId")
    for kind, number in counters.items():
        if type(number) is not int or number <= maximum[kind] or number < 1:
            raise Conflict("next_number 必须大于该字母所有已用编号，不回收旧号")


def stored_metadata(root: Path, versions: tuple[str, ...] = (VERSION,)) -> dict:
    data = read_optional(root / META)
    if data is None:
        return {}
    value = parse_object(data, "项目元数据")
    if value.get("agent_collab_version") not in versions:
        raise Conflict(f"不自动升级或接管已有版本；0.3.0 请显式运行 upgrade 到 {VERSION}，其他版本须另行迁移")
    if value["agent_collab_version"] == VERSION:
        validate_runtime(value.get("runtime"))
    return value


def configuration(name: str, goal: str | None) -> dict:
    return {"schema_version": 2, "agent_collab_version": VERSION, "name": name, "goal": goal, "task_state": STATE}


def receipt_for(config: dict, local_skill: dict[str, bytes]) -> bytes:
    return json_bytes({
        "schema_version": 2, "version": VERSION,
        "managed_sha256": {key: sha(value) for key, value in local_skill.items()},
        "configuration_sha256": sha(json_bytes(config)),
        "agents_block_sha256": sha(block()), "mutable": [STATE, META + "#/runtime"],
    })


def plan_install(root: Path) -> tuple[dict[str, bytes], dict[str, bytes | None]]:
    root = safe_path(root)
    if root.exists() and not root.is_dir():
        raise Conflict(f"项目路径不是目录：{root}")
    stored_metadata(root)
    desired = skill_files()
    snapshots = {rel: read_optional(root / rel) for rel in desired}
    conflicts = [rel for rel, data in snapshots.items() if data is not None and data != desired[rel]]
    if conflicts:
        raise Conflict("已有 Skill 文件内容不同，不覆盖：" + ", ".join(conflicts))
    return {rel: data for rel, data in desired.items() if snapshots[rel] is None}, snapshots


def plan_init(root: Path, name: str | None, goal: str | None) -> tuple[dict[str, bytes], dict[str, bytes | None]]:
    root = safe_path(root)
    if root.exists() and not root.is_dir():
        raise Conflict(f"项目路径不是目录：{root}")
    metadata = stored_metadata(root)
    name = name if name is not None else metadata.get("name", root.name)
    goal = goal if goal is not None else metadata.get("goal")
    if not isinstance(name, str) or (goal is not None and not isinstance(goal, str)):
        raise Conflict("已有项目名称或目标格式不正确，未修改")
    name = validate_text(name, "项目名称", 160)
    if goal is not None:
        goal = validate_text(goal, "目标", 4000)
    local_skill = skill_files()
    config = configuration(name, goal)
    desired = {META: json_bytes({**config, "runtime": empty_runtime()}), **local_skill}
    receipt = receipt_for(config, local_skill)
    # 先读取全部目标并收集冲突；任何 mkdir/write 都发生在此函数返回以后。
    snapshots: dict[str, bytes | None] = {}
    errors = []
    for rel in ("AGENTS.md", *desired, STATE, RECEIPT):
        try:
            snapshots[rel] = read_optional(root / rel)
        except (Conflict, OSError) as exc:
            errors.append(str(exc))
    if errors:
        raise Conflict("\n".join(errors))
    agents = snapshots["AGENTS.md"]
    existing_receipt = snapshots[RECEIPT]
    if existing_receipt is not None:
        if existing_receipt != receipt:
            errors.append("初始化清单与当前参数或版本不一致；不自动升级、改名、改目标或接管清单")
        for rel, expected in desired.items():
            # 只有 runtime 是可变身份数据；项目配置仍由清单保护。
            matches = ({key: value for key, value in metadata.items() if key != "runtime"} == config
                       if rel == META else snapshots[rel] == expected)
            if not matches:
                errors.append(f"受管文件缺失或已修改：{rel}")
        if snapshots[STATE] is None:
            errors.append(f"任务状态文件缺失：{STATE}")
        if agents is None or agents.count(BEGIN) != 1 or agents.count(END) != 1:
            errors.append("AGENTS.md 受管标记缺失或重复")
        else:
            start, finish = agents.index(BEGIN), agents.index(END)
            if finish < start or agents[start:finish + len(END)] != block().rstrip(b"\n"):
                errors.append("AGENTS.md 受管段已修改")
        if errors:
            raise Conflict("\n".join(errors))
        return {}, snapshots
    for rel in (META, STATE):
        if snapshots[rel] is not None:
            errors.append(f"已有文件不属于本次初始化，不接管：{rel}")
    for rel, data in local_skill.items():
        if snapshots[rel] is not None and snapshots[rel] != data:
            errors.append(f"已有 Skill 文件内容不同，不覆盖：{rel}")
    if agents is not None and (BEGIN in agents or END in agents):
        errors.append("AGENTS.md 已有受管标记但缺少匹配清单，不自动接管")
    if errors:
        raise Conflict("\n".join(errors))
    previous = agents or b""
    separator = b"" if not previous else (b"\n" if previous.endswith(b"\n") else b"\n\n")
    changes = {"AGENTS.md": previous + separator + block(), **desired, STATE: initial_state(), RECEIPT: receipt}
    return {rel: data for rel, data in changes.items() if snapshots[rel] != data}, snapshots


def plan_upgrade(root: Path) -> tuple[dict[str, bytes], dict[str, bytes | None]]:
    metadata = stored_metadata(root, ("0.3.0", VERSION))
    if metadata.get("agent_collab_version") == VERSION:
        return plan_init(root, None, None)
    if not metadata:
        raise Conflict("upgrade 需要已有 0.3.0 项目；新项目请用 init")
    expected_keys = {"schema_version", "agent_collab_version", "name", "goal", "task_state"}
    if set(metadata) != expected_keys or metadata["schema_version"] != 1 or metadata["task_state"] != STATE:
        raise Conflict("旧项目结构不匹配，不自动接管")
    name, goal = metadata["name"], metadata["goal"]
    if not isinstance(name, str) or (goal is not None and not isinstance(goal, str)):
        raise Conflict("旧项目参数无效")
    if validate_text(name, "项目名称", 160) != name or (goal is not None and validate_text(goal, "目标", 4000) != goal):
        raise Conflict("旧项目参数需要人工核对")
    snapshots = {rel: read_optional(root / rel) for rel in ("AGENTS.md", META, STATE, RECEIPT, SKILL, SKILL_SCRIPT)}
    if any(value is None for value in snapshots.values()):
        raise Conflict("旧项目受管文件或任务状态缺失，不升级")
    old = parse_object(snapshots[RECEIPT], "旧初始化清单")
    if set(old) != {"schema_version", "version", "managed_sha256", "agents_block_sha256", "mutable"} or old["schema_version"] != 1 or old["version"] != "0.3.0" or old["mutable"] != [STATE]:
        raise Conflict("旧初始化清单不匹配")
    expected_hashes = {rel: sha(snapshots[rel]) for rel in (META, SKILL, SKILL_SCRIPT)}
    if old["managed_sha256"] != expected_hashes:
        raise Conflict("旧受管文件已修改；未写入，不升级")
    agents = snapshots["AGENTS.md"]
    if agents.count(BEGIN) != 1 or agents.count(END) != 1 or agents.index(END) < agents.index(BEGIN):
        raise Conflict("旧 AGENTS 标记缺失、重复或顺序错误")
    start, finish = agents.index(BEGIN), agents.index(END) + len(END)
    if sha(agents[start:finish] + b"\n") != old["agents_block_sha256"]:
        raise Conflict("旧 AGENTS 受管段已修改；未写入，不升级")
    # 备份也参加完整预检和回滚；保留旧身份原文，但不转成可见 ready 角色。
    changes = {}
    for rel, data in list(snapshots.items()):
        backup = ".agent-collab/backups/0.3.0/" + rel
        snapshots[backup] = read_optional(root / backup)
        if snapshots[backup] is not None:
            raise Conflict("升级备份路径已存在，不覆盖：" + backup)
        changes[backup] = data
    runtime = empty_runtime()
    runtime["legacy_identity_source"] = "backups/0.3.0/.agent-collab/tasks.md"
    config, local_skill = configuration(name, goal), skill_files()
    changes.update({
        "AGENTS.md": agents[:start] + block().rstrip(b"\n") + agents[finish:],
        META: json_bytes({**config, "runtime": runtime}), **local_skill,
        RECEIPT: receipt_for(config, local_skill),
    })
    return changes, snapshots


def upgrade(args: argparse.Namespace) -> int:
    root = safe_path(Path(args.directory))
    changes, snapshots = plan_upgrade(root)
    if args.check or args.dry_run:
        print("升级预检通过，未写入：" + (", ".join(changes) if changes else "已是当前版本"))
        return 1 if args.check and changes else 0
    write_plan(root, changes, snapshots)
    print("升级文件就绪；旧任务和段外规则保留，旧身份未转为可见就绪。" if changes else "已是当前版本，无需变更。")
    return 0


def role_record(args: argparse.Namespace) -> int:
    root = safe_path(Path(args.directory))
    changes, snapshots = plan_init(root, None, None)
    if changes:
        raise Conflict("先完成文件 init，再记录角色")
    metadata = parse_object(snapshots[META], "项目元数据")
    runtime = metadata["runtime"]
    roles = runtime["roles"]
    if args.command == "role-reserve":
        key = validate_text(args.key, "请求 key", 160)
        existing = [role for role in roles if role["kind"] == args.kind and role["key"] == key]
        if existing:
            print(json.dumps(existing[0], ensure_ascii=False))
            return 0
        if args.kind in ("E", "P") and any(role["kind"] == args.kind and role["status"] != "retired" for role in roles):
            raise Conflict("已有未退役角色，先核验和接续，不重复预留：" + args.kind)
        number = runtime["next_number"][args.kind]
        if runtime["legacy_identity_source"] and number == 1 and args.after is None:
            raise Conflict("升级项目须先审阅旧身份；用 --after <该字母历史最大已用号，确认从未用过则为 0> 避免回收旧号")
        if args.after is not None:
            if args.after < number - 1:
                raise Conflict("--after 不能降低已用编号上界")
            number = args.after + 1
        ident = f"{args.kind}{number:03d}"
        role = {"id": ident, "kind": args.kind, "key": key, "title": ident + "｜" + ROLE_NAMES[args.kind], "status": "reserved",
                **dict.fromkeys(("thread_id", "client_thread_id", "host_id", "project_id", "cwd", "evidence", "authorization_source", "authorization_scope"))}
        roles.append(role)
        runtime["next_number"][args.kind] = number + 1
    else:
        matching = [role for role in roles if role["id"] == args.id]
        if not matching:
            raise Conflict("未预留该角色编号")
        role = matching[0]
        if role["status"] == "retired":
            raise Conflict("已退役编号保留，不复活或分配给新身份")
        if (args.authorization_source is None) != (args.authorization_scope is None):
            raise Conflict("授权来源和范围须同时提供；不默认继承旧范围")
        for field in ("thread_id", "client_thread_id", "host_id", "project_id", "cwd", "evidence", "title", "authorization_source", "authorization_scope"):
            value = getattr(args, field)
            if value is not None:
                value = validate_text(value, field, 4000)
                if field in ("thread_id", "client_thread_id", "host_id", "project_id") and role[field] not in (None, value):
                    raise Conflict("已记录身份不能替换：" + field)
                role[field] = value
        if args.status == "reserved" and role["status"] != "reserved":
            raise Conflict("不能将已开始的创建退回预留状态；先查证未知结果")
        role["status"] = args.status
    validate_runtime(runtime)
    updated = json_bytes(metadata)
    if updated != snapshots[META]:
        write_plan(root, {META: updated}, snapshots)
    print(json.dumps(role, ensure_ascii=False))
    print("仅保存本地记录；脚本未调用宿主或核验身份，ready 必须来自真实宿主回读。")
    return 0


def write_plan(root: Path, changes: dict[str, bytes], snapshots: dict[str, bytes | None]) -> None:
    # 写前再次核对，防止普通的并发编辑。此检查不是恶意进程隔离或文件系统事务。
    for rel, old in snapshots.items():
        if read_optional(root / rel) != old:
            raise Conflict(f"预检后文件发生变化：{rel}")
    completed: list[str] = []
    created_dirs: list[Path] = []
    try:
        for rel, data in changes.items():
            target = root / rel
            safe_path(target)
            missing = []
            parent = target.parent
            while not parent.exists():
                missing.append(parent)
                parent = parent.parent
            for directory in reversed(missing):
                directory.mkdir()
                created_dirs.append(directory)
            if read_optional(target) != snapshots[rel]:
                raise Conflict(f"写入前文件发生变化：{rel}")
            if snapshots[rel] is None:
                with target.open("xb") as stream:
                    completed.append(rel)
                    stream.write(data)
            else:
                # 原文件直到替换成功前保持完整。
                fd, temp = tempfile.mkstemp(prefix=".agent-collab-", dir=target.parent)
                try:
                    with os.fdopen(fd, "wb") as stream:
                        stream.write(data)
                    shutil.copymode(target, temp)
                    if read_optional(target) != snapshots[rel]:
                        raise Conflict(f"替换前文件发生变化：{rel}")
                    os.replace(temp, target)
                    completed.append(rel)
                finally:
                    if os.path.exists(temp):
                        os.unlink(temp)
    except Exception:
        # 尽力恢复本次已写文件；不删除第三方随后写入的不同内容。
        for rel in reversed(completed):
            target = root / rel
            try:
                if read_optional(target) == changes[rel]:
                    if snapshots[rel] is None:
                        target.unlink()
                    else:
                        target.write_bytes(snapshots[rel])
            except OSError:
                pass
        for directory in reversed(created_dirs):
            try:
                directory.rmdir()
            except OSError:
                pass
        raise


def git_plan(root: Path, requested: bool) -> str | None:
    if not requested:
        return None
    routing = sorted(key for key in os.environ if key.upper() in GIT_ROUTING_ENV)
    if routing:
        raise Conflict("--git 拒绝可能重定向仓库或注入配置的环境变量：" + ", ".join(routing)
                       + "；请在未设置这些变量的进程重试，未写入任何文件")
    if not shutil.which("git"):
        raise Conflict("--git 需要可用的 Git；未写入任何文件")
    # 拒绝在已有仓库内隐式创建嵌套仓库，也不修改已有仓库配置。
    for directory in [root, *root.parents]:
        marker = directory / ".git"
        if os.path.lexists(marker):
            safe_path(marker)
            return "已有 Git 标记，跳过 Git 初始化"
    return "git init"


def install(args: argparse.Namespace) -> int:
    root = safe_path(Path(args.directory))
    changes, snapshots = plan_install(root)
    if args.check:
        print("Skill 安装检查：" + ("尚未完整安装" if changes else "文件一致"))
        return 1 if changes else 0
    if args.dry_run:
        print("安装预检通过，未写入：" + (", ".join(changes) if changes else "无需变更"))
        return 0
    write_plan(root, changes, snapshots)
    print("项目本地 Skill 已安装：" + str(root / ".agents/skills/agent-collab"))
    print("在该项目 Codex 对话中说“初始化协作”。安装本身不创建项目状态或 Agent。")
    return 0


def init(args: argparse.Namespace) -> int:
    root = safe_path(Path(args.directory))
    changes, snapshots = plan_init(root, args.name, args.goal)
    git_action = git_plan(root, args.git)
    if args.check:
        if changes or git_action == "git init":
            print("检查未就绪：需要初始化；未写入任何文件")
            return 1
        print("检查通过：受管文件一致，任务状态文件保留")
        return 0
    if args.dry_run:
        print("预检通过（dry-run，未写入）：" + (", ".join(changes) if changes else "无需变更"))
        if git_action:
            print(git_action)
        return 0
    write_plan(root, changes, snapshots)
    if git_action == "git init":
        result = subprocess.run(["git", "init", str(root)], cwd=root, capture_output=True, text=True, encoding="utf-8", errors="replace")
        if result.returncode:
            print("协作文件已初始化，但 Git 初始化失败；文件已保留，可检查后重试。", file=sys.stderr)
            print(result.stderr, file=sys.stderr)
            return 3
        verified = subprocess.run(["git", "-C", str(root), "rev-parse", "--show-toplevel"],
                                  capture_output=True, text=True, encoding="utf-8", errors="replace")
        try:
            # Windows 的短路径名与 Git 返回的长路径名可指向同一目录。
            matches = (safe_path(root / ".git").is_dir() and not verified.returncode
                       and bool(verified.stdout.strip()) and root.samefile(verified.stdout.strip()))
        except (Conflict, OSError):
            matches = False
        if not matches:
            print("协作文件已初始化，但未能确认 Git 仓库位于目标目录；文件已保留，请检查现状。", file=sys.stderr)
            return 3
        print("已显式初始化 Git")
    elif git_action:
        print(git_action)
    print(("文件初始化完成：" if changes else "文件已就绪，无需变更：") + str(root))
    print("文件就绪不等于角色齐备；宿主须读取 " + META + "，按真人授权核验可见 E/P 聊天，不能用隐藏子 Agent 替代。")
    return 0


def pack(args: argparse.Namespace) -> int:
    output = safe_path(Path(args.output))
    sidecar = output.with_name(output.name + ".sha256")
    safe_path(sidecar)
    if output.suffix.lower() != ".zip":
        raise Conflict("输出文件须以 .zip 结尾")
    if os.path.lexists(output) or os.path.lexists(sidecar):
        raise Conflict("输出 ZIP 或校验文件已存在；不覆盖")
    if not output.parent.is_dir():
        raise Conflict("输出目录必须已经存在")
    files = {}
    for rel in PUBLIC_FILES:
        data = read_optional(SOURCE / rel)
        if data is None:
            raise Conflict(f"白名单文件缺失：{rel}")
        files[rel] = data
    if files["VERSION"].decode("utf-8").strip() != VERSION:
        raise Conflict("VERSION 与程序版本不一致")
    files["MANIFEST.json"] = json_bytes({
        "name": "agent-collab", "version": VERSION,
        "files": {rel: sha(data) for rel, data in files.items()},
    })
    buffer = io.BytesIO()
    # 固定条目顺序和时间；相同输入在同一 Python/zlib 环境得到相同字节。
    with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for rel, data in sorted(files.items()):
            info = zipfile.ZipInfo("agent-collab-" + VERSION + "/" + rel, (2024, 1, 1, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            info.create_system = 3
            info.external_attr = 0o100644 << 16
            archive.writestr(info, data)
    zip_data = buffer.getvalue()
    with output.open("xb") as stream:
        stream.write(zip_data)
    with sidecar.open("x", encoding="utf-8", newline="\n") as stream:
        stream.write(sha(zip_data) + "  " + output.name + "\n")
    print(f"已导出 agent-collab {VERSION}：{output}")
    print("SHA256 " + sha(zip_data))
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="agent-collab：初始化项目本地协作入口，或导出可分发 ZIP。")
    parser.add_argument("--version", action="version", version=VERSION)
    subparsers = parser.add_subparsers(dest="command", required=True)
    initializer = subparsers.add_parser("init", help="安全初始化新目录或已有项目")
    initializer.add_argument("directory", nargs="?", default=".", help="目标项目目录，默认当前目录；支持中文和空格，拒绝 '..' 跳转")
    initializer.add_argument("--name", help="默认复用已有名称，新项目使用目录名；不自动改名")
    initializer.add_argument("--goal", help="可选单行目标；仅记录，不自动执行业务")
    mode = initializer.add_mutually_exclusive_group()
    mode.add_argument("--dry-run", action="store_true", help="只预检并显示将发生的变更")
    mode.add_argument("--check", action="store_true", help="只验证已有初始化，未初始化返回 1")
    initializer.add_argument("--git", action="store_true", help="显式初始化 Git；已有仓库内跳过")
    initializer.set_defaults(func=init)
    installer = subparsers.add_parser("install", help="只安装完整项目本地 Skill，不初始化项目或创建 Agent")
    installer.add_argument("directory", nargs="?", default=".", help="目标项目目录，默认当前目录")
    install_mode = installer.add_mutually_exclusive_group()
    install_mode.add_argument("--dry-run", action="store_true", help="安装预检，不写入")
    install_mode.add_argument("--check", action="store_true", help="检查完整 Skill 是否已安装")
    installer.set_defaults(func=install)
    upgrader = subparsers.add_parser("upgrade", help="显式升级完整且未改动的 0.3.0 项目，保存原字节备份")
    upgrader.add_argument("directory", nargs="?", default=".")
    upgrade_mode = upgrader.add_mutually_exclusive_group()
    upgrade_mode.add_argument("--dry-run", action="store_true")
    upgrade_mode.add_argument("--check", action="store_true")
    upgrader.set_defaults(func=upgrade)
    reserve = subparsers.add_parser("role-reserve", help="只预留项目内角色编号；不创建聊天")
    reserve.add_argument("directory")
    reserve.add_argument("kind", choices=ROLE_NAMES)
    reserve.add_argument("--key", required=True, help="本项稳定请求标识；重复 key 返回原记录")
    reserve.add_argument("--after", type=int, help="已核验的该字母历史最大已用号；只可提高编号下界")
    reserve.set_defaults(func=role_record)
    recorder = subparsers.add_parser("role-record", help="只记录宿主返回结果；不执行或验证宿主操作")
    recorder.add_argument("directory")
    recorder.add_argument("id")
    recorder.add_argument("--status", required=True, choices=sorted(ROLE_STATES))
    for field in ("thread-id", "client-thread-id", "host-id", "project-id", "cwd", "evidence", "title", "authorization-source", "authorization-scope"):
        recorder.add_argument("--" + field)
    recorder.set_defaults(func=role_record)
    packer = subparsers.add_parser("pack", help="按固定白名单导出 ZIP 与 SHA256")
    packer.add_argument("--output", required=True, help="不已存在的 .zip 路径；父目录须已存在")
    packer.set_defaults(func=pack)
    args = parser.parse_args(argv)
    try:
        return args.func(args)
    except (Conflict, OSError) as exc:
        print("未完成：" + str(exc), file=sys.stderr)
        return 2


if __name__ == "__main__":
    if sys.version_info < (3, 10):
        sys.exit("需要 Python 3.10 或更新版本")
    sys.exit(main())
