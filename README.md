# 每日新闻早报（GitHub 自动版）

每天北京时间 **10:05** 自动抓取国内 / 国际新闻各 10 条 + 人民日报时评 3 篇，生成网页并通过 GitHub Pages 发布，手机扫码即可看。

**零成本、不依赖你的电脑、不消耗任何 AI 额度。**

## 文件说明

| 文件 | 作用 |
|---|---|
| `.github/workflows/daily-news.yml` | 定时任务：每天 10:05 跑脚本 → 生成网页 → 提交回仓库 |
| `daily_news.py` | 抓取 + 排版脚本（纯标准库，无需安装依赖） |
| `docs/index.html` | 生成的早报网页（每次运行自动覆盖，也是 GitHub Pages 的入口） |

## 部署步骤（约 10 分钟，全程不用写代码）

### 1. 建一个公开仓库

- 打开 https://github.com/new
- Repository name 填 `daily-news`（可自定义）
- 选 **Public**（免费账号的 GitHub Pages 必须公开仓库）
- 不要勾选「Add a README file」等初始化项，保持空仓库
- 点 **Create repository**

### 2. 上传文件

把本目录里的这 4 样东西传到仓库根目录（保持目录结构）：

```
.github/workflows/daily-news.yml
daily_news.py
docs/index.html
.gitignore
```

最简单的方式：直接把整个 `github-deploy` 文件夹的内容拖进 GitHub 网页的「uploading an existing file」界面。
（也可以用 GitHub Desktop / git 命令行，看个人习惯。）

### 3. 开启网页托管（GitHub Pages）

- 进入仓库 → **Settings** → 左侧 **Pages**
- **Source** 选 `Deploy from a branch`
- **Branch** 选 `main`，目录选 `/docs`
- 点 **Save**

等几十秒，页面顶部会出现一行绿字，给出你的访问地址：

```
https://<你的用户名>.github.io/daily-news/
```

### 4. 立刻试一次（不用等明天）

- 仓库顶部 **Actions** 标签 → 左侧 **每日新闻早报** → 右侧 **Run workflow** → 点绿按钮
- 大约 1~2 分钟后变绿（✓），刷新上面的链接，应该就是今天的早报

## 每天早上怎么看

把步骤 3 拿到的链接生成二维码（任何二维码工具都行），员工扫码即看；或直接收藏链接加到手机桌面。

> 链接固定不变，所以二维码一次生成永久有效。

## 几个已知注意点

- **时区已处理**：cron 写的是 UTC `05 02 * * *`，等于北京时间 10:05；脚本也设了 `TZ=Asia/Shanghai`，日期不会跨日错位。
- **GitHub 在境外，抓取国内新闻源偶尔可能失败**：若某天页面停在旧日期或内容为空，多半是抓取被网络干扰，去 **Actions** 页面看运行日志即可定位。长期不稳可换国内源或加代理（脚本无需改，只动 workflow）。
- **免费额度足够**：公开仓库每月 Actions 不限分钟数，这个任务每天约 1 分钟，完全免费。
- **仓库长期不活动会自动停用定时任务**：本方案每天自动提交一次，天然保持活跃，不会被停用。

## 手动触发 / 排查

- 想马上更新：Actions 页面点 **Run workflow**
- 想看日志：Actions 页面点某次运行 → 看每一步输出
- 想暂停推送：Actions 页面 → 该 workflow → ··· → **Disable workflow**
