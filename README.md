# Multimo

无人机视频智能分析系统，中关村新域新质创新挑战赛参赛项目。

接入无人机视频、遥感影像和文档，自动抽取关键帧并识别目标，把画面对齐到地理坐标，支持混合检索和基于证据的问答。

## 仓库结构

这个仓库用 Git Submodules 管理两个子项目：

| 路径 | 仓库 | 技术栈 |
| :--- | :--- | :--- |
| `multimo-backend/` | [MeVeryHandsome/multimo-backend](https://github.com/MeVeryHandsome/multimo-backend) | GoFrame v2.10.4、PostgreSQL 16 + pgvector、FFmpeg |
| `multimo-frontend/` | [MeVeryHandsome/multimo-frontend](https://github.com/MeVeryHandsome/multimo-frontend) | React 19、Vite、TypeScript、Tailwind CSS v4 |

主仓库本身放部署文件、目标检测服务、环境变量模板、测试数据脚本和文档：

```
multimo/
├── docker-compose.yml      postgres(pgvector) + detector + backend + frontend
├── docker-compose.gpu.yml  检测服务改用 GPU（可选）
├── detector/               VisDrone 目标检测服务（YOLO，FastAPI）
├── .env.example            环境变量模板
├── deploy/postgres/        数据库初始化（扩展）
├── docs/                   架构、API、测试报告
├── scripts/                测试数据准备、一键演示（demo.sh）、接口文档生成
└── testdata/               测试数据（大文件不入库，只提交清单和评测集）
```

## 快速开始

```bash
git clone --recurse-submodules https://github.com/MeVeryHandsome/multimo.git
```

```bash
cd multimo && cp .env.example .env
```

编辑 `.env`，至少填好 `POSTGRES_PASSWORD`、`VLM_API_KEY`、`DECISION_API_KEY`。

**一键演示**：下载并校验测试数据、启动全部服务、登记并处理评测语料、运行示例检索和问答，最后跑完整评测并把报告保存到 `demo-output/`：

```bash
scripts/demo.sh
```

只启动服务：

```bash
docker compose up -d --build
```

检测服务默认用 CPU，首次启动会下载约 110 MB 的模型权重（国内可在 `.env` 中把 `DETECTOR_WEIGHTS_URL` 的 `huggingface.co` 换成 `hf-mirror.com`）。有 NVIDIA GPU 时：

```bash
docker compose -f docker-compose.yml -f docker-compose.gpu.yml up -d --build
```

检测服务部署在另一台 GPU 服务器上时，把 `.env` 里的 `DETECTOR_BASE_URL_DOCKER` 改成它的地址即可。推荐在 GPU 服务器上运行按 1280 输入微调的模型（三尺度融合，漏检率 22.2%，默认 CPU 模型为 25.7%），部署方法见 [detector/README.md](detector/README.md#在-gpu-服务器上部署)。

| 地址 | 内容 |
| :--- | :--- |
| http://localhost:8080 | 前端 |
| http://localhost:8000/swagger | Swagger UI |
| http://localhost:8000/api.json | OpenAPI 文档 |
| http://localhost:8000/api/v1/system/health | 健康检查 |

国内网络构建镜像时，可以在 `.env` 里把 `GOPROXY` 改成 `https://goproxy.cn,direct`，把 `NPM_REGISTRY` 改成 `https://registry.npmmirror.com`，把 `PIP_INDEX_URL` 改成 `https://pypi.tuna.tsinghua.edu.cn/simple`。

## 评测

先下载测试数据（自动校验 MD5，可重复执行）：

```bash
scripts/prepare_testdata.sh
```

登记评测语料、处理完队列后运行全部 6 项评测（报告写到数据卷的 `reports/<时间>/`）：

```bash
docker compose exec backend multimo testsuite -register
```

也可以在服务运行时通过接口触发，进度和报告见 `/api/v1/testsuite/status`、`/api/v1/testsuite/reports/latest/html`：

```bash
curl -X POST localhost:8000/api/v1/testsuite/run -H 'Content-Type: application/json' -d '{}'
```

指标定义见 [架构说明](docs/ARCHITECTURE.md#评测internalservicetestsuite)，实测结果见 [测试报告](docs/TEST_REPORT.md)。

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

- [架构说明](docs/ARCHITECTURE.md)：组件、处理流程、模型分工、Decision 接口约定、前端、评测方法
- [目标检测服务](detector/README.md)：模型选择、阈值标定、接口和部署
- [测试数据](testdata/README.md)：来源、镜像、许可、下载方式（`scripts/prepare_testdata.sh`）
- [评测集](testdata/benchmark/README.md)：50 条检索 query、50 个问答对及判定规则
- [接口文档](docs/API.md)：全部接口的参数、返回字段和示例（`scripts/gen_api_docs.py` 由 OpenAPI 生成）
- [测试报告](docs/TEST_REPORT.md)：实测结果、指标对照和调优记录

## 进度

| 阶段 | 内容 | 状态 |
| :--- | :--- | :--- |
| P0 | 仓库骨架、submodule、compose、环境变量模板 | 完成 |
| P1 | 测试数据下载与校验、评测集 | 完成 |
| P2 | 后端基础：数据库、AI 网关、goroutine 池、文件解析 | 完成 |
| P3 | 关键帧、目标识别、空间对齐 | 完成 |
| P4 | 混合检索、RAG 问答 | 完成 |
| P5 | 自动化评测与报告 | 完成 |
| P6 | 前端界面、目标检测服务 | 完成 |
| P7 | 一键演示、API 文档、测试报告 | 完成 |
