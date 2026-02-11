# hoa-prServer

一个对外提供 JSON 接口的服务端，用于 QQ bot / Web 端触发“智能化 PR”流程。

## 本地运行

```bash
cd hoa-prServer-main
uv sync
uv run uvicorn hoa_prserver.app:app --app-dir src --host 0.0.0.0 --port 8000 --reload
```

## Docker 运行

```bash
docker build -t hoa-prserver:dev .
docker run --rm -p 8000:8000 \
  -e ORG_NAME=HITSZ-OpenAuto \
  -e GITHUB_TOKEN="$GITHUB_TOKEN" \
  -e API_KEY="$API_KEY" \
  -v "$(pwd)/data:/data" \
  hoa-prserver:dev
```

或者用 compose：

```bash
docker compose up
```

说明：容器里包含 `git`，用于自动 clone/push 开 PR；SQLite 默认挂载到 `./data/`。

## API

- `GET /health`
  - 返回服务状态

- `POST /v1/readme/render`
  - 入参：`{ "toml": "..." }`
  - 出参：`{ "readme_md": "..." }`

### 最小闭环（final schema）：确保 PR 存在并可更新

- `POST /v1/pr/ensure`
  - 语义：幂等（以稳定分支名 `bot/rdme-<repo>` 为准）。
    - 已有同 head 的 open PR：push 更新分支内容（PR 自动更新），返回 `pr_updated`。
    - 未有 PR：创建 PR，返回 `pr_created`。
    - 若内容无变化：返回 `no_change`（若已有 PR 则一并返回 `pr_url`）。
  - 入参：`{ course_code, course_name?, repo_type?, repo_name?, toml? }`
    - `toml` 为 **final schema** 的 `readme.toml` 文本。
    - 若不传 `toml`，服务端会尝试读取 `FINAL_DIR/<course_code>/readme.toml`。
  - 出参：`{ status, pr_url?, branch?, source?, request_id? }`

### 仓库查询 / 提交 PR

这些接口用于 Web/QQ bot 完成“查仓库 -> 拿/编辑 TOML -> 服务端生成 README 并自动开 PR”。

- `GET /v1/org/repos?q=&limit=`
  - 列出组织下仓库（可用 `q` 做简单过滤）

- `GET /v1/courses/lookup?course_code=...&course_name=...&repo_type=normal|multi-project&repo_name=...`
  - 若仓库存在：返回 `repo` 基础字段 + 仓库里的 `readme.toml`（若不存在则返回模板）
  - 若仓库不存在：返回模板 TOML，`exists=false`

说明：默认用 `course_code` 作为仓库名；如果仓库名与课程号不一致，请传 `repo_name=<实际仓库名>`。

- `POST /v1/courses/submit`
  - 入参：`{ course_code, course_name, repo_type, toml, repo_name? }`
  - 若仓库存在：创建分支、写入 `readme.toml` + 生成 `README.md`、push 并开 PR，返回 `pr_url`
  - 若仓库不存在：写入 pending 队列并（可选）发邮件提醒管理员创建仓库，返回 `request_id`

- `POST /v1/courses/submit_ops`
  - 用途：服务端读取仓库里的 `readme.toml`（不存在则用模板），按 `ops` 做“局部修改”，再自动开 PR。
  - 入参：`{ repo_name?, course_code, course_name, repo_type, ops, dry_run? }`
  - 说明：`dry_run=true` 时只返回 `toml`（不创建 PR）。

> 说明：`submit_ops` 适合 Bot/前端做“结构化编辑”。当前 final schema 下：
> - `append_section_item` / `update_section_item` 的 `section` 指 `[[sections]].title`（按标题匹配）
> - `update_section_item.index` 指 `[[sections.items]]` 的下标
> 若你已经拿到了完整 final TOML，也可以直接用 `POST /v1/pr/ensure` 提交并确保 PR。

## Web 前端（无构建）

服务端内置一个静态页面，访问：`http://localhost:8000/web/`

功能（简化版工作流）：

- 左侧搜索栏：调用 `GET /v1/courses/index`（支持按 repo/course_code/course_name 搜索）
- 点击条目：右侧展示当前 README（`GET /v1/courses/readme`，必要时由 readme.toml 渲染生成）
- 在“编辑 TOML”里直接修改 `readme.toml` 文本，并用“渲染预览”查看效果
- 点击“提交（确保 PR）”：调用 `POST /v1/pr/ensure`，幂等创建/更新同一个 PR

注意：页面右上角的 API Base 不要包含 `/web/`，应填 `http://localhost:8000` 或留空（同源）。

- `GET /v1/requests/{request_id}`
  - 查询 pending 状态（`waiting_repo` / `pr_created` / `failed` 等）

## 环境变量

- `ORG_NAME`：GitHub 组织名（默认 `HITSZ-OpenAuto`）
- `ALLOWED_REPOS`：可选；仓库白名单（逗号分隔）。设置后仅允许这些 repo
- `GITHUB_TOKEN`：需要 repo 写权限（创建分支、push、开 PR）
- `API_KEY`：可选；设置后所有接口需携带 Header：`X-Api-Key: <API_KEY>`
- `HOA_PRSERVER_DB`：SQLite 路径（默认 `./data/hoa_prserver.sqlite3`）
- `POLL_INTERVAL_SECONDS`：轮询 pending 的周期（默认 3600 秒）

final 目录（可选）：

- `FINAL_DIR`：指向包含 `final/<CODE>/readme.toml` 的目录（例如挂载到容器内的 `/data/final` 或本机 `E:\\Code\\final`）。用于 `POST /v1/pr/ensure` 在未提供 `toml` 时读取。

邮件提醒（可选，未配置会自动跳过）：

- `SMTP_HOST` / `SMTP_PORT` / `SMTP_USER` / `SMTP_PASSWORD` / `SMTP_FROM` / `ADMIN_EMAIL`

## 代码结构（每个文件是干什么的）

- `src/hoa_prserver/app.py`：FastAPI 路由 + 启动时的 pending 轮询任务
- `src/hoa_prserver/github_client.py`：GitHub REST API（列仓库 / 查仓库 / 读文件 / 开 PR）
- `src/hoa_prserver/pr_flow.py`：clone -> 写 TOML -> 生成 README -> commit/push -> 开 PR
- `src/hoa_prserver/render.py`：纯渲染能力（把 TOML 转为 README.md 文本）
- `src/hoa_prserver/db.py`：SQLite pending_requests 表（仓库不存在时入队等待）
- `src/hoa_prserver/settings.py`：从环境变量加载配置
- `src/hoa_prserver/auth.py`：可选 API Key（Header `X-Api-Key`）
- `src/hoa_prserver/emailer.py`：SMTP 发邮件提醒管理员（可选）
- `src/hoa_prserver/toml_templates.py`：normal / multi-project 的最小模板
- `scripts/convert_toml_to_readme.py`：与 RDME 工具链一致的 TOML->README 转换脚本
