# agent-collab

版本 **0.4.0**。一次安装完整 Skill，在各项目发出协作初始化请求：生成项目入口，再建立或核验用户可见的 **E001｜协作环境** 和 **P001｜日常伙伴**。P 统一收口，小改自做；T 执行、R 审核按实际任务和新聊天授权创建，不预建角色池。

## 一次安装，项目内直接使用

独立公开仓库为 [polaris0326/agent-collab](https://github.com/polaris0326/agent-collab)，Skill 路径为 `skills/agent-collab`。安装时选择该仓库实际已发布版本对应的完整提交 SHA，并核对其中存在完整 Skill；若尚无可安装文件则等待发布，不能把开发仓库的 SHA 当成独立仓库版本。以下占位符须替换成真实发布值，不代表已验证该版本的安装。

```text
用 skill-installer 从 https://github.com/polaris0326/agent-collab 安装 skills/agent-collab，固定到 <发布时填写的独立仓库完整提交 SHA>
```

官方安装器默认放到 `$CODEX_HOME/skills/agent-collab`，未设置时为 `~/.codex/skills/agent-collab`，目标已存在则停止。也可将经校验的 ZIP 中整个 `skills/agent-collab` 文件夹复制到该位置；必须同时包含 SKILL.md 和 scripts/agent_collab.py，不能只复制说明文件。已有安装先备份、核对用户改动，再由用户明确授权更新；本产品脚本不更新用户级安装。

安装后的下一轮，在实际目标项目中推荐说：

```text
初始化协作，当前聊天作为 E 协作环境，新建 P 日常伙伴聊天；允许本项目后续明确任务中按需创建 T/R 可见聊天及同任务通信。
```

日常不必记住仓库或 ZIP。也支持“初始化协作”“搭建协作环境”“init协作”或显式 `$agent-collab 初始化`。若真人已明确授权，直接在其范围内继续；只有短语而宿主要求明确新聊天请求时，Agent 会在创建前询问一次，也可一次确认后续按需 T/R 和同任务通信。只同意 E/P 时不扩大范围。已确认的真人授权来源/范围记在绑定实际项目的 P 元数据中，交给 P 接续；拒绝、未答或后来撤销不构成持续授权。Skill 正文不能替用户授权，也不能保证所有宿主都零确认。重复请求先查已有角色，不能多建 P。

匹配基于 Skill description，保持默认隐式调用；普通应用 init、数据库初始化、npm/git init、关键词解释不触发本流程。[官方 Skill 文档](https://learn.chatgpt.com/docs/build-skills) 说明了匹配机制。未安装 Skill 时可用来源备用入口 [INITIALIZE.md](INITIALIZE.md)。

## 你会看到什么

文件成功后，E 优先复用发起初始化的当前用户聊天，核对并归属实际项目；P 是独立的用户可见聊天。E 处理环境和入口转交，P 负责日常任务的唯一收口。你可以继续在 E 提任务，也可直接进入 P。T/R 仅在真实任务需要且真人明确授权新聊天时创建；同项返修由原 T 完成，原 R 复核。

创建使用宿主用户侧聊天 API，不能用隐藏子 Agent 代替。项目须能由宿主实际项目列表定位；脚本不注册项目。当前聊天身份、host、projectId、cwd 缺失时报告待核验，不能凭标题猜 E 就绪。异步返回 clientThreadId 只代表 pending，不能当 threadId 调用其他工具。调用结果未知先查证，不能重复创建。

**“文件就绪”与“E/P 齐备”分别报告。** 脚本退出 0 不证明聊天存在或可见。只有真实宿主回读及 P 就绪回执齐备才称整体就绪；缺能力、缺授权或身份不明时停止相关角色操作。初始化不执行 goal，也不是独立审核。

## 交付与验收

需要用户验收的任务由 P 主动在自己的聊天给出成果入口、固定候选、简短检查步骤和预期结果、已测与未测边界，以及明确的接受/反馈方式；不以“待接受”三个字代替交接。用户明确接受后才记录完成，其他待办不会随之自动启动。

本次为 0.4.0 的指令修订，脚本与文件格式不变，以公开提交 SHA 区分修订；旧 v0.4.0 标签和 ZIP 保留原字节。已安装或已初始化的项目不会自动获得修订，更新时先核对并备份原 Skill；项目本地副本需同步保护清单中的对应哈希，保留角色和任务状态，不重做初始化或删除保护清单。

## 文件与运行前提

需要 **Python 3.10+**，产品运行与测试均只依赖标准库。命令中的 python 可按环境换成 python3 或 py -3。作者实测 Windows 11 / CPython 3.13.1；Python 3.10、macOS/Linux 未实机测试。本版仅做隔离文件测试和工具说明核对，真实桌面聊天创建、项目归属、自动发现未实跑。

| 项目相对路径 | 用途与编辑边界 |
| --- | --- |
| `AGENTS.md` | 易发现协作入口和 E/P/T/R 规则；只管理明确 begin/end 段，段外原字节保留。 |
| `.agents/skills/agent-collab/` | 完整、自包含 Skill，含唯一脚本；项目移动后可继续从相对位置使用。 |
| `.agent-collab/project.json` | 受管项目配置＋可变 runtime 身份。角色编号、threadId/clientThreadId、host、project、cwd、证据集中存于此。 |
| `.agent-collab/tasks.md` | 唯一任务状态入口，日常可编辑；只引用角色编号，不另存活跃身份。可指向既有外部任务系统而不双写。 |
| `.agent-collab/manifest.json` | 保护配置、Skill 和 AGENTS 受管段的指纹；不锁死 runtime 或 tasks。 |
| `.agent-collab/backups/0.3.0/` | 仅显式升级时创建的原字节备份；不是第二份活动任务状态。 |

宿主应从正在读取的 Skill 目录定位脚本，以**实际项目根**为 cwd 和显式目标。用户级 Skill 安装目录仅是工具来源，不能当项目。文件路径自包含；身份中的绝对 cwd 是实际宿主事实，项目移动后需重新核验。

## 文件命令与显式升级

```shell
python agent_collab.py init "我的项目"
python agent_collab.py init "我的项目" --check
python agent_collab.py upgrade "已有 0.3.0 项目" --dry-run
python agent_collab.py upgrade "已有 0.3.0 项目"
```

分发根脚本是兼容壳，调用 Skill 内同一实现。直接 init 安装完整项目 Skill 和配置；可选 install 仅安装项目 Skill。所有文件命令支持相应 dry-run/check，不写文件；init 默认用目录名/空目标，重复运行复用已存参数。显式 --name/--goal 不是改名或改目标开关。

**0.3.0 → 0.4.0 仅显式 upgrade。** 先保留旧包、检查改动，可用 dry-run 核对计划。完整预检旧清单、受管文件、AGENTS 标记和新备份路径；任何冲突均无损停止。成功时备份原 AGENTS、元数据、清单、tasks 和完整 Skill，保留原 tasks 字节及 AGENTS 段外规则，只更新受管内容。重复 upgrade 不再写入。0.1/0.2/未知版本无自动迁移；受管文件曾改动时先人工审阅，不删除清单强行接管。

旧 tasks 的 P 身份原文保留为历史；新 runtime 从未核验状态开始，绝不把旧隐藏 P 标为可见 ready。首次分配每类角色前核对历史最大已用编号，以 `role-reserve ... --after <最大号>` 继续；确认无旧号才填 0，无法确认就停。身份 helper 的参数与接续步骤见 [Skill](skills/agent-collab/SKILL.md)。它只保存记录，不认证填写者的身份声明；测试中的虚构身份仅测试存储规则。

每项目每字母编号递增，已用号不回收。重复 kind/key 返回原记录；已有未退役 E/P 阻止再分配第二个。pending、unknown 和退役记录保留；已绑定真实身份不可改成另一聊天。角色只存身份/生命周期，业务状态仍在 tasks。

所有写入均先完整冲突预检。同内容重复 init/upgrade 保持字节和 mtime；受管编辑、损坏标记、缺失文件或参数冲突停止，日常 tasks/runtime 与用户规则保留。脚本不解决用户规则的语义冲突；Agent 应按当前授权处理。路径拒绝 `..`、链接/Windows 重解析点、多硬链接目标和非 UTF-8 文件。不要多个进程并发操作同一项目；有二次检查和尽力回滚，但不是文件系统事务或恶意进程隔离。

Git 仅显式 --git 才初始化；已有父仓库内跳过。检测到可能重定向仓库的 GIT_DIR/GIT_WORK_TREE 等环境变量，写前拒绝，不改环境。脚本不提交、推送、注册原生项目、调用聊天 API、安装全局 Skill/Hook 或服务。

退出码：0=文件成功或无需变更，1=check 发现仍需操作，2=参数/冲突/文件失败，3=文件已成功但 Git 初始化或目标核验失败。撤回前保存任务和身份，再仅移除已确认由本工具生成且无需保留的文件及 AGENTS 受管段；升级备份可供人工恢复，不自动覆盖后续编辑。无自动卸载/回滚命令。

## 导出与许可

```shell
python agent_collab.py pack --output "agent-collab-0.4.0.zip"
python -B -m unittest discover -s tests -v
```

pack 仅在完整分发目录运行；固定白名单为 README、INITIALIZE.md、VERSION、兼容入口、完整 Skill 两个文件、测试，另生成 MANIFEST.json。不会打包 .git、缓存、项目身份或任务。ZIP 内含各文件 SHA256，旁边 .sha256 为包哈希；发布时另绑定独立仓库完整提交 SHA。可直接将本目录作为新仓库根，不自动发布。

仓库用于获取和安装本工具；公开可获取不等于授予开源许可。许可证仍待权利人选择，当前不承诺任何未授予的使用、修改或再分发许可。
