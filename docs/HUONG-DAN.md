# Hướng dẫn sử dụng harness-kit (tiếng Việt)

harness-kit cài một **harness** vào dự án để coding agent (Claude Code, Codex, Cursor…) làm việc có kiểm chứng:
agent không tự tuyên bố "xong" được nữa, mà harness chạy lệnh verification và ghi bằng chứng. Với Claude Code,
bộ kit thêm một lớp `.claude/` gồm subagent, lệnh `/hk-*`, hook và status line — tương tự claudekit nhưng tập trung
vào kỷ luật harness engineering (WIP=1, pass-gate một chiều, maker–checker, clean state).

## 1. Cài đặt

Yêu cầu: Python ≥ 3.10 (không cần cài thư viện nào). Node ≥ 18 chỉ cần nếu dùng `npx`.

```bash
git clone https://github.com/duonghuyhieu/harness.git ~/harness-kit
python ~/harness-kit/harness.py install /đường/dẫn/dự-án       # mọi hệ điều hành
~/harness-kit/install.sh /đường/dẫn/dự-án                      # macOS / Linux / Git Bash
.\harness-kit\install.ps1 D:\du-an                             # Windows PowerShell
npx github:duonghuyhieu/harness install .                      # qua npm
```

Tùy chọn hay dùng:

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

Sau khi cài: mở dự án trong Claude Code, gõ **`/hk-init`** để chạy giai đoạn khởi tạo (baseline H001–H006), hoặc
**`/hk-help`** để xem bản đồ lệnh. Kiểm tra cài đặt: `harness doctor .`

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
| PreToolUse | **chặn** sửa tay trạng thái harness; **hỏi** trước khi ghi ngoài scope của feature active; không bao giờ tự "allow" |
| PostToolUse | ghi lại tệp/lệnh phiên đã chạm vào `.harness/activity.jsonl` |
| PreCompact | ghi `<project>/.harness/compact-snapshot.md` trước khi nén ngữ cảnh |
| Stop | chặn kết thúc lượt khi còn debug code, file rác, feature list bị sửa tay (không chặn hai lần liên tiếp) |
| SessionEnd | ghi nhận phiên kết thúc có handoff hay không |

Hook là rào chắn rẻ, không phải cổng: thiếu Python hoặc runtime thì hook bỏ qua (exit 0) kèm ghi chú. Cổng thật là
`verify` và `exit-check`. Tắt hook cho một phiên: `HARNESS_HOOKS=off`. Đổi chế độ kiểm tra scope trong
`.harness/config.json`: `"scope_guard": "ask" | "deny" | "off"`. Nên bật thêm sandbox của chính Claude Code để
cô lập filesystem và mạng — harness không thay thế sandbox cấp hệ điều hành.

## 4. Cập nhật và gỡ cài

```bash
harness update .        # làm mới .claude/ + scripts/harness.py; tệp bạn đã sửa được giữ, bản mới vào .harness/proposals/
harness update . --force   # ghi đè cả tệp harness bạn đã sửa
harness uninstall .     # gỡ các tệp harness chưa bị sửa trong .claude/ và các mục harness trong settings.json
harness doctor .        # kiểm tra sức khỏe cài đặt (chỉ đọc)
```

`uninstall` không xóa `AGENTS.md`, `PROGRESS.md`, `feature_list.json`, `docs/`… vì đó đã là nội dung của dự án.

## 5. Khắc phục sự cố

| Triệu chứng | Cách xử lý |
|---|---|
| Hook không chạy | `harness doctor .` — xem dòng "python for hooks" và "hooks registered"; cài lại với `--python "<lệnh>"` |
| Claude Code báo "workspace has not been trusted" | mở dự án một lần ở chế độ tương tác và chấp nhận hộp thoại trust (permission allow mới có hiệu lực) |
| Bị hỏi khi ghi ngoài scope | đúng thiết kế: thêm tệp vào `--scope` của feature, hoặc ghi nhận thành feature mới; hoặc `scope_guard: "off"` |
| Stop hook chặn kết thúc | đọc danh sách lỗi (debug code, file rác…), sửa rồi chạy `python scripts/harness.py exit-check` |
| Lệnh test/lint phát hiện sai | sửa `commands` trong `.harness/config.json` (nguồn sự thật duy nhất của lệnh) |
