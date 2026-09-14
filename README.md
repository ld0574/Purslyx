# Purslyx


<img src="docs/00-overview/brand/logo.png" alt="Purslyx：提着公文包的猞猁" width="180" />

> **AI that hunts your next opportunity.**

Purslyx 是面向求职者与招聘方的 AI 助手，聚焦有依据的岗位匹配、真实自然的简历表达与面试准备。名字来自 **Pursue（追求）+ Lynx（猞猁）**，寓意敏锐发现机会、主动采取行动。提着公文包的猞猁是项目的品牌形象。

## 目录规范

文档先按用途分区，再把同一类产物集中到稳定的子目录。以下目录已建立，空目录使用 `.keep` 占位，加入实际文件后移除占位文件。

```text
Purslyx/
├── README.md                      # 项目简介、目录规范与常用入口
├── docs/
│   ├── 00-overview/               # 项目概览
│   │   ├── brand/                 # 项目命名说明与品牌素材
│   │   └── event/                 # 黑客松招募信息、规则与赛题说明
│   ├── 01-product/                # 产品资料
│   │   ├── specs/                 # 需求说明、原始记录、模块与页面清单
│   │   ├── visual/                # 视觉规范、配色对比与早期探索稿
│   │   └── prototypes/            # Web 与浏览器侧高保真稿及评审截图
│   ├── 02-technical/              # 技术选型、架构、接口与运行部署说明
│   │   ├── api/                   # API 总规范、路由索引与八个模块的字段级契约
│   │   ├── database/              # 数据库总规范、模块索引与模块数据库设计
│   │   └── modules/               # 技术模块索引与八个模块的实现设计
│   ├── 03-operations/             # 运营
│   │   ├── sop/                   # SOP
│   │   └── templates/             # 可复用的脱敏简历、岗位 JD、示例输入与输出
│   ├── 04-presentations/          # 演示截图、操作录屏、路演材料与作品提交说明
│   └── 99-archive/                # 已停用或被替代的历史文档，注明归档原因
├── src/
│   ├── web/                       # Web 端：页面、交互与应用逻辑
│   ├── extension/                 # 浏览器插件：清单、内容脚本、弹窗与后台逻辑
│   └── userscript/                # 篡改猴脚本：脚本入口、页面增强与网页交互
├── tests/                         # 核心流程测试、异常输入用例及测试专用数据
├── scripts/                       # 启动、数据准备、打包和部署等辅助脚本
├── infra/                         # 部署配置，如容器编排、反向代理配置
├── .gitignore                     # 本地配置、缓存与生成文件的忽略规则
└── LICENSE                        # Apache-2.0 许可证
```

## 文件约定

- **文档按内容归类**：项目命名与品牌素材放 `00-overview/brand/`，赛事资料放 `00-overview/event/`；产品需求放 `01-product/specs/`，视觉规范与探索稿放 `01-product/visual/`，可操作高保真稿和截图放 `01-product/prototypes/`；实现方式放 `02-technical/`，运营 SOP 与脱敏模板放 `03-operations/`，演示与提交材料放 `04-presentations/`。
- **命名直观、层级稳定**：例如 `01-product/specs/需求说明.md`、`01-product/prototypes/高保真产品稿.html`、`02-technical/本地运行.md`。同一主题维护一份文件，日常修改由 Git 记录；整份文档停用或被替代时移入 `99-archive/`。
- **说明与实际文件分开**：部署说明放 `docs/02-technical/`，部署配置放 `infra/`，执行脚本放 `scripts/`；脱敏样例放 `docs/03-operations/templates/`，展示材料放 `docs/04-presentations/`。简历及截图中的个人信息需脱敏。
- **代码按使用端划分**：Web 端放 `src/web/`；浏览器侧预留 `src/extension/`（浏览器插件）和 `src/userscript/`（篡改猴脚本），按实际实现选择其中一种方案。
- **根目录保持简洁**：放项目入口说明、工具配置和环境变量示例；各端独立使用的依赖清单与构建配置放在对应源码目录，共用配置放根目录。
- **本地文件**如真实密钥、`.env`、依赖目录、日志和构建产物不提交，按实际工具补充 `.gitignore`；环境变量模板使用 `.env.example`，只写变量名和占位值。

现有资料：[需求说明（已评审）](docs/01-product/specs/需求说明.md) · [首版模块与页面清单（已评审）](docs/01-product/specs/首版模块与页面清单.md) · [首版高保真产品稿](docs/01-product/prototypes/高保真产品稿.html) · [篡改猴高保真产品稿](docs/01-product/prototypes/篡改猴高保真产品稿.html) · [技术架构（已评审）](docs/02-technical/技术架构.md) · [API 设计索引](docs/02-technical/api/API设计索引.md) · [技术模块设计索引](docs/02-technical/modules/技术模块设计索引.md) · [数据库设计规范（已评审）](docs/02-technical/database/数据库设计规范.md) · [模块数据库设计索引](docs/02-technical/database/数据库设计索引.md) · [项目起点与复用说明](docs/04-presentations/项目起点与复用说明.md) · [原始需求记录](docs/01-product/specs/需求记录.md) · [视觉方案](docs/01-product/visual/视觉方案.md) · [配色对比](docs/01-product/visual/视觉对比.html) · [项目命名说明](docs/00-overview/brand/Purslyx%20项目命名说明.md) · [黑客松信息](docs/00-overview/event/黑客松信息.md) · [黑客松规则与赛题说明](docs/00-overview/event/黑客松规则与赛题说明.md)

明日 SDD 材料：[Feature Spec](docs/01-product/specs/Feature%20Spec-资料确认与可复核匹配.md) · [需求—接口—代码追溯矩阵](docs/02-technical/SDD追溯矩阵-最小演示.md) · [201 实施计划](docs/02-technical/实施计划-201最小演示.md) · [演示脚本](docs/04-presentations/明日SDD演示脚本.md) · [测试与验收记录](docs/04-presentations/201最小演示测试与验收记录.md) · [变更记录](docs/04-presentations/201最小演示变更记录.md)

本项目采用 [Apache License 2.0](LICENSE)。

## 明日可直接演示的最小链路

当前先交付一条能落地的纵向切片：提交简历文字和岗位 JD → 确认不可变资料版本 → 生成
带证据、覆盖率和条件状态的匹配报告。根路径页面使用隔离的 `/api/v1/demo` 短入口，
正式链路使用带认证的 `/api/v1` 接口，并由 `scripts/smoke_201.py` 复核；页面不需要
Node.js 构建，邮件、Celery 和 Redis Worker 暂不阻塞演示。

数据库固定直连【本地开发环境.md】中的 201 PostgreSQL，禁止 SQLite。密码不要拼进 URL（本
地密码含 URL 特殊字符），用 `PGPASSWORD` 注入：

```bash
uv venv .venv
uv pip install -e '.[test]'
scripts/start_201_local.sh
```

启动脚本只从被 Git 忽略的 `docs/02-technical/deployment/本地开发环境.md` 读取
PostgreSQL 密码并注入当前进程；默认监听 8001（如端口空闲，也可用
`PURSLYX_PORT=8000 scripts/start_201_local.sh`）。

另开终端执行：

```bash
PYTHONPATH=src .venv/bin/python scripts/smoke_201.py
```

看到 `"database": {"backend": "postgresql"...}`、`"analysis_status": "available"` 即可
进入浏览器打开 <http://127.0.0.1:8001/> 演示。完整的 201 连接约定见
[201 环境部署约定](docs/02-technical/deployment/201环境部署.md)。
