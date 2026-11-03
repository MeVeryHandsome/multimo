# Multimo

无人机视频智能分析系统，中关村新域新质创新挑战赛参赛项目。

接入无人机视频、遥感影像和文档，自动抽取关键帧并识别目标，把画面对齐到地理坐标，支持混合检索和基于证据的问答。

## 仓库结构

这个仓库用 Git Submodules 管理两个子项目：

| 路径 | 仓库 | 技术栈 |
| :--- | :--- | :--- |
| `multimo-backend/` | [MeVeryHandsome/multimo-backend](https://github.com/MeVeryHandsome/multimo-backend) | GoFrame v2.10.4、PostgreSQL 16 + pgvector、FFmpeg |
| `multimo-frontend/` | [MeVeryHandsome/multimo-frontend](https://github.com/MeVeryHandsome/multimo-frontend) | React 19、Vite、TypeScript、Tailwind CSS v4 |

主仓库本身放部署文件、环境变量模板、测试数据脚本和文档：

```
multimo/
├── docker-compose.yml      postgres(pgvector) + backend + frontend
├── .env.example            环境变量模板
├── deploy/postgres/        数据库初始化（扩展）
├── docs/                   架构、API、测试报告
├── scripts/                测试数据准备、一键演示
└── testdata/               测试数据（大文件不入库，只提交清单和评测集）
```

## 快速开始

```bash
git clone --recurse-submodules https://github.com/MeVeryHandsome/multimo.git
```

```bash
cd multimo && cp .env.example .env
```

编辑 `.env`，至少填好 `POSTGRES_PASSWORD`、`VLM_API_KEY`、`DECISION_API_KEY`，然后：

```bash
docker compose up -d --build
```

| 地址 | 内容 |
| :--- | :--- |
| http://localhost:8080 | 前端 |
| http://localhost:8000/swagger | Swagger UI |
| http://localhost:8000/api.json | OpenAPI 文档 |
| http://localhost:8000/api/v1/system/health | 健康检查 |

国内网络构建镜像时，可以在 `.env` 里把 `GOPROXY` 改成 `https://goproxy.cn,direct`，把 `NPM_REGISTRY` 改成 `https://registry.npmmirror.com`。

## 子模块工作流

已经 clone 但没带 `--recurse-submodules`：

```bash
git submodule update --init --recursive
```

把子模块更新到各自远端 `main` 的最新提交：

```bash
git submodule update --remote --merge
```

修改子模块代码时，先在子模块里提交并推送，再回到主仓库提交新的子模块指针：

```bash
cd multimo-backend && git checkout main && git commit -am "..." && git push
```

```bash
cd .. && git add multimo-backend && git commit -m "Bump backend" && git push
```

## 本地开发（不用 Docker）

需要 Go 1.24+、Node 22+、FFmpeg，以及装了 pgvector 的 PostgreSQL 16。

```bash
cd multimo-backend && set -a && source ../.env && set +a && make run
```

```bash
cd multimo-frontend && npm install && npm run dev
```

## 文档

- [架构说明](docs/ARCHITECTURE.md)：组件、处理流程、模型分工、Decision 接口约定、评测方法
- [测试数据](testdata/README.md)：来源、镜像、许可、下载方式（`scripts/prepare_testdata.sh`）
- [评测集](testdata/benchmark/README.md)：50 条检索 query、50 个问答对及判定规则
- `docs/API.md`：接口文档（由 OpenAPI 生成，开发中）
- `docs/TEST_REPORT.md`：实测结果与指标对照（开发中）

## 进度

| 阶段 | 内容 | 状态 |
| :--- | :--- | :--- |
| P0 | 仓库骨架、submodule、compose、环境变量模板 | 完成 |
| P1 | 测试数据下载与校验、评测集 | 完成 |
| P2 | 后端基础：数据库、AI 网关、goroutine 池、文件解析 | 完成 |
| P3 | 关键帧、目标识别、空间对齐 | 未开始 |
| P4 | 混合检索、RAG 问答 | 未开始 |
| P5 | 自动化评测与报告 | 未开始 |
| P6 | 前端界面 | 未开始 |
| P7 | 联调、一键演示、API 文档、测试报告 | 未开始 |
