# Hướng dẫn sử dụng harness-kit (tiếng Việt)

harness-kit cài một **harness** vào dự án để coding agent (Claude Code, Codex, Cursor…) làm việc có kiểm chứng:
agent không tự tuyên bố "xong" được nữa, mà harness chạy lệnh verification và ghi bằng chứng. Với Claude Code,
bộ kit thêm một lớp `.claude/` gồm subagent, lệnh `/hk-*`, hook và status line — tương tự claudekit nhưng tập trung
vào kỷ luật harness engineering (WIP=1, pass-gate một chiều, maker–checker, clean state).

## 1. Cài đặt — một lệnh, cài xong là chạy

Mở terminal **trong thư mục dự án** và chạy **một** trong các lệnh sau. Lệnh sẽ tạo luôn thư mục `.claude/` (agents,
skills `/hk-*`, hooks, status line, settings) cùng các tệp harness ngay trong dự án. Sau đó mở thư mục bằng Claude Code
là dùng được — không cần chạy thêm gì.

```bash
# Windows (PowerShell)
irm https://raw.githubusercontent.com/duonghuyhieu/harness-kit/main/install.ps1 | iex

# macOS / Linux / Git Bash
curl -fsSL https://raw.githubusercontent.com/duonghuyhieu/harness-kit/main/install.sh | bash

# npm (Node ≥ 18)
npx github:duonghuyhieu/harness-kit                  # cài vào thư mục hiện tại
npm install -D github:duonghuyhieu/harness-kit       # tự cài khi npm install (giống claudekit)
```

Yêu cầu duy nhất: có Python ≥ 3.10 trên PATH (không cần cài thư viện) — hook chạy bằng Python. Chạy lại đúng lệnh đó
là cập nhật kit (tệp bạn đã sửa được giữ nguyên). `/hk-init` là **tùy chọn**: chạy một lần nếu muốn agent điền kiến
thức dự án (AGENTS.md, docs/) và lập baseline verification; `/hk-help` liệt kê mọi lệnh.

Cài vào thư mục khác hoặc từ bản clone: `python harness.py install D:\du-an` (hoặc `install.ps1 D:\du-an`,
`install.sh /duong/dan`).

Tùy chọn hay dùng (khi chạy `harness.py install`):

| Tùy chọn | Ý nghĩa |
|---|---|
| `--level minimal\|standard\|full` | mức tài liệu sinh ra (mặc định `standard`) |
| `--dry-run` | chỉ in kế hoạch, không ghi gì |
| `--no-statusline` | không cài status line của harness |
| `--python "py -3"` | lệnh Python mà hook sẽ dùng (mặc định tự dò) |
| `--global` | cài skills + agents vào `~/.claude` cho mọi dự án (không có hook) |
| `--git-init`, `--commit` | khởi tạo git / commit checkpoint các tệp harness mới |

**An toàn khi cài:** không bao giờ ghi đè tệp của bạn. `AGENTS.md`, `CLAUDE.md`, feature list đã có → giữ nguyên,
bản của harness nằm ở `.harness/proposals/`. `<project>/.claude/settings.json` đã có → gộp từng mục (hook, permission,
status line của bạn được giữ). Mọi tệp harness cài vào `.claude/` được ghi vào manifest `<project>/.claude/harness-kit.json`
kèm sha256, nên `update` và `uninstall` biết tệp nào bạn đã sửa.

Sau khi cài: mở dự án trong Claude Code — hook, status line và các lệnh `/hk-*` hoạt động ngay. (Tùy chọn) gõ
**`/hk-init`** để chạy giai đoạn khởi tạo (baseline H001–H006), **`/hk-help`** để xem bản đồ lệnh. Kiểm tra cài đặt:
`python ~/.harness-kit/harness.py doctor .` (macOS/Linux/Git Bash) · `python "$HOME\.harness-kit\harness.py" doctor .` (PowerShell) · `npx github:duonghuyhieu/harness-kit doctor` (npm). Lệnh in ở dòng "Health check any time" sau khi cài là lệnh đúng cho máy của bạn.

Trong dự án mới, feature list có sẵn các feature nền của harness (H001…): làm cho lệnh setup/test chạy được, viết
test đầu tiên… `next` sẽ chọn chúng trước; muốn làm feature của mình ngay thì dùng `next --id F012`.

## 2. Quy trình làm việc hằng ngày

| Bước | Lệnh trong Claude Code | Runtime tương ứng (dùng được với mọi agent) |
|---|---|---|
| Vào phiên | `/hk-start` | `python scripts/harness.py start` |
| Lập kế hoạch | `/hk-plan <yêu cầu>` | `python scripts/harness.py add --id F1 --behavior "..." --verify "<lệnh>" --scope "src/x/*"` |
| Làm một feature | `/hk-implement` | `next` → làm → `try F1` → review → `verify F1` |
| Review độc lập | `/hk-review` | subagent `hk-reviewer` (chỉ đọc, phiên mới) |
| Pass-gate | `/hk-verify F1` | `python scripts/harness.py verify F1` |
| Ra phiên | `/hk-handoff` | `handoff` → viết PROGRESS.md → `exit-check` |

Lệnh khác: `/hk-audit` (chấm 5 hệ con), `/hk-diagnose` (quy lỗi về lớp harness), `/hk-loop` (vòng lặp tự trị
maker → reviewer → gate), `/hk-graph` (khi nào cần graph nhiều vai trò), `/hk-optimize` (refactor legacy),
`/hk-observe` (log, trace, golden path), `/hk-clean` (clean state, đơn giản hóa harness), `/hk-docs` (repo là
system of record).

Ba quy tắc cốt lõi:
1. **WIP=1** — chỉ một feature `active`; `next` từ chối khi còn feature đang làm hoặc VCR < 1.
2. **Pass-gate một chiều** — chỉ `verify` mới chuyển feature sang `passing` (chạy lệnh verification + check toàn dự
   án, ghi bằng chứng); hook chặn mọi chỉnh sửa tay `status`/`evidence`.
3. **Maker không tự chấm** — `hk-reviewer` (phiên mới, chỉ đọc) review trước `verify`, vì `passing` không quay lại.

## 3. Hook làm gì

| Sự kiện | Hành vi |
|---|---|
| SessionStart | tiêm trạng thái: số feature, VCR, feature active + lệnh verify, bước tiếp, lần check gần nhất; sau compaction tiêm thêm snapshot |
| UserPromptSubmit | nhắc 1 dòng khi có feature active (chống lặp 10 phút) |
| PreToolUse | **chặn** sửa tay trạng thái harness, sửa cách chấm của feature đã bắt đầu (`verification`, `scope`, `activated_commit`) và id trùng; **hỏi** trước khi ghi ngoài scope của feature active và trước khi sửa cấu hình của chính harness (`<project>/.harness/config.json`, `<project>/.claude/settings.json`); **luôn chặn** các cách tắt hook (`git commit --no-verify`/`-n`, `core.hooksPath`, `GIT_CONFIG_*`, `claude --safe-mode`/`--bare`, `CLAUDE_CONFIG_DIR`, `disableAllHooks`); **hỏi** trước khi làm yếu "thước đo" — sửa cấu hình lint/test/CI, `.harness/rules.json`, bỏ assertion hoặc thêm skip trong file test (qua Edit/Write), xóa, di chuyển hoặc sửa tại chỗ file test bằng lệnh shell (`checker_guard`); **chặn** việc tự chạy lệnh hook (`harness.py hook-*`, `harness_hook.py`); khi khởi chạy `hk-reviewer` thì ghi lại nội dung lúc review bắt đầu, và từ chối prompt đặt sẵn verdict; không bao giờ tự "allow" |
| PostToolUse | ghi lại tệp/lệnh phiên đã chạm vào `.harness/activity.jsonl` |
| PreCompact | ghi `<project>/.harness/compact-snapshot.md` trước khi nén ngữ cảnh |
| Stop | chặn kết thúc lượt khi còn debug code, file rác, feature list bị sửa tay, thay đổi chưa được verify, hoặc khi phiên đã verify/commit mà chưa cập nhật PROGRESS.md (không chặn hai lần liên tiếp; nếu vẫn cho dừng khi còn lỗi thì ghi thành incident); cảnh báo người dùng, không chặn, khi câu trả lời cuối có dấu hiệu biện hộ ("pre-existing issue", "should work", "tests pass" mà không chạy check) |
| SessionEnd | ghi nhận phiên kết thúc có handoff hay không; việc chưa verify hoặc chưa handoff thành incident, hiện ở đầu phiên sau |
| SubagentStop | ghi verdict của mỗi lần `hk-reviewer` chạy (`VERDICT: APPROVE / REJECT / UNCERTAIN`) kèm dấu vân tay của thay đổi đã review: với `review_gate: require` (mặc định khi cài cho Claude Code), `verify` cần một APPROVE cho đúng nội dung hiện tại; APPROVE chỉ được tính khi transcript của chính Claude Code cho lượt reviewer đó (trong `~/.claude/projects`) có verdict này và prompt khớp với một lần khởi chạy hk-reviewer đã ghi; `verify <id> --no-review "<lý do>"` được ghi thành incident. Đây là bằng chứng chống việc bỏ qua review, không phải ranh giới bảo mật trước một agent cố tình viết code để giả mạo (docs/DECISIONS.md, 2.3.1) |

Hook là rào chắn rẻ, không phải cổng: thiếu Python hoặc runtime thì hook bỏ qua (exit 0) kèm ghi chú. Cổng thật là
`verify` và `exit-check`. Khi hook bị lỗi, Claude Code hiện cảnh báo (`systemMessage`); muốn guard lỗi thì *từ chối*
thay vì cho qua, đặt `"guard_failure": "closed"`. Tắt hook cho một phiên: `HARNESS_HOOKS=off`; tắt riêng từng hook:
`"hooks_off": ["prompt"]` (tắt `guard` hay `stop` đều được ghi log). Các khóa khác trong `.harness/config.json`:
`"scope_guard": "ask" | "deny" | "off"`, `"instruction_guard": "ask" | "off"` (hỏi trước khi sửa AGENTS.md,
CLAUDE.md, `.claude/agents|skills|rules/`). `python scripts/harness.py validate` kiểm tra cấu hình;
`trace --grep hook-` xem mọi ngữ cảnh đã tiêm (kèm session id và hash). Nên bật thêm sandbox của chính Claude Code để
cô lập filesystem và mạng — harness không thay thế sandbox cấp hệ điều hành.

## 4. Cập nhật và gỡ cài

Cập nhật: **chạy lại đúng lệnh cài đặt** (khi dự án đã có manifest, lệnh cài hoạt động như `update`). Các lệnh còn
lại gọi CLI của kit — `python ~/.harness-kit/harness.py` (macOS/Linux/Git Bash), `python "$HOME\.harness-kit\harness.py"`
(PowerShell), hoặc `npx github:duonghuyhieu/harness-kit` (npm):

```bash
python ~/.harness-kit/harness.py update .          # làm mới .claude/ + scripts/harness.py; tệp bạn đã sửa được giữ, bản mới vào .harness/proposals/
python ~/.harness-kit/harness.py update . --force  # ghi đè cả tệp harness bạn đã sửa
python ~/.harness-kit/harness.py uninstall .       # gỡ các tệp harness chưa bị sửa trong .claude/ và các mục harness trong settings.json
python ~/.harness-kit/harness.py doctor .          # kiểm tra sức khỏe cài đặt (chỉ đọc)
```

`uninstall` không xóa `AGENTS.md`, `PROGRESS.md`, `feature_list.json`, `docs/`… vì đó đã là nội dung của dự án.

## 5. Khắc phục sự cố

| Triệu chứng | Cách xử lý |
|---|---|
| Hook không chạy | chạy doctor (mục 1) — xem dòng "python for hooks" và "hooks registered"; cài lại với `--python "<lệnh>"` |
| Claude Code hỏi trước `try`/`verify` | đúng thiết kế: hai lệnh này chạy lệnh verification lấy từ tệp agent sửa được; muốn tự duyệt thì tự thêm luật allow |
| doctor báo CLAUDE.md không import AGENTS.md | thêm dòng `@AGENTS.md` lên đầu CLAUDE.md của bạn, nếu không Claude Code không thấy luật harness |
| Claude Code báo "workspace has not been trusted" | mở dự án một lần ở chế độ tương tác và chấp nhận hộp thoại trust (permission allow mới có hiệu lực) |
| Bị hỏi khi ghi ngoài scope | đúng thiết kế: thêm tệp vào `--scope` của feature, hoặc ghi nhận thành feature mới; hoặc `scope_guard: "off"` |
| Stop hook nói "changes not verified" | chạy `python scripts/harness.py try <id>` sau lần sửa cuối, rồi kết thúc lượt |
| Đã xóa một luật/hook của harness | `update` giữ nguyên việc xóa đó; muốn khôi phục: chạy lại lệnh cài với `--force` |
| Muốn xem trước thay đổi | thêm `--diff` vào lệnh cài/cập nhật (chạy thử, in diff từng tệp) |
| Stop hook chặn kết thúc | đọc danh sách lỗi (debug code, file rác…), sửa rồi chạy `python scripts/harness.py exit-check` |
| Lệnh test/lint phát hiện sai | sửa `commands` trong `.harness/config.json` (nguồn sự thật duy nhất của lệnh) |
