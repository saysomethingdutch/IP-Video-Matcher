# WO-101 吞噬星空：易存云取证下载，以及电脑网页录屏的台账

- 签发：Owner　执行：**Codex**　时间盒：**1 个工作日**，机器时间另计
- 分支：从 `main` 切出 `tsxk/wo-101-fetch-ledger`
- 交付说明：`docs/workorders/WO-101-RESULT.md`，不超过 1 页
- 背景和口径：见 `DECISIONS.md` 的 D-056，以及 `/Volumes/Element/ip-video-matcher-field-test/guidance/吞噬星空视频对比说明_文字还原版.docx`

## 0. 案件

- **工作表**：`/Volumes/Element/ip-video-matcher-field-test/field-test/【G组】吞噬星空视频比对.xlsx`，"沈瑞曦"这个 sheet 的**后 400 行**（序号 7813–8212）。
- **只读**：原表只读。以后导出时写到副本里。
- **取证形式**：全部是易存云（公证云"见证实录"）录屏。录的是一台云端 Windows 电脑：先做环境检查，再用 Chrome 打开 B 站网页播放。不再是相机拍手机屏。
- **示例录屏**：`field-test/videos/1–17.mp4`。其中 13–17 是易存云格式，1920×1080。

## 1. 易存云下载器（`pilot/ezcun/`）

**Owner 已经查实的情况（2026-09-27）：**
- 分享页 `https://www.ezcun.com/web/#/share?evidenceId=<ID>` 打开后，页面会自己调用 `POST /web/api/evid/getEvidencePreviewById`。
- 这个接口返回 JSON，其中：
  - `fileUrl`：华为云 OBS 的**带时效签名**的 mp4 直链；
  - `fileSize`、`sonEvid.duration`（毫秒）；
  - `startWitnessTime`、`finishedWitnessTime`；
  - `originalMd5`、`encryptedMd5`。
- 用脚本直接调这个接口，会被站点的 CloudWAF 拦截，返回 HTTP 418"访问被拦截"。用真实浏览器打开页面，则一切正常。

**要求：**
1. **只能用真实浏览器**，例如 Playwright 驱动系统 Chrome，**有界面模式**。打开分享页，从页面自己的网络响应里读出上面这些字段。
   - **严禁**伪造签名、改请求头、换 IP 去绕过防火墙。
   - 一旦出现验证码或人机校验，**立即停下**，报告给所有者。
   - 放慢节奏：每页之间间隔 ≥ 5 秒。
2. **先只抓元数据**，400 行全部抓，写 `manifest.csv`：
   - 行号、evidenceId、文件大小、时长、`startWitnessTime`、`finishedWitnessTime`、链接过期时间；
   - 报告总大小。
3. **下载已获所有者同意**（D-057）。元数据抓完后接着做：
   - 先试下 5 个，核对大小和 md5：`originalMd5`、`encryptedMd5` 各对一下，写明哪个对得上；
   - 5 个都对了，再下全部。
   - 下载目标：外置盘 `/Volumes/Element/ip-video-matcher-field-test/evidence/<序号>.mp4`。
   - 支持断点续传；签名过期了就重新打开页面，拿新链接。
   - 外置盘剩余空间低于 20 GB 就停。
4. **L 列（取证完成时间）**：先用接口的 `finishedWitnessTime`。
   - 抽 5 行，对照电子数据保全证书，也就是 N 列 block-view 页里的 PDF。
   - 一致的话，就以接口时间为准；
   - 格式按指引写：`yyyy/m/d hh:mm:ss`。

## 2. 电脑网页录屏的台账（`pilot/webledger/`）

这是新模块，不要硬改七时吉祥那套针对手机拍屏的 ledger。

**要读出的信息**：
- 地址栏里的 BV；
- 网页标题 → D 列；
- 播放量、发布时间、UP 主；
- "视频选集"的分P 名称和时长；
- 播放器的时间读数：只有鼠标悬停时才出现；
- 页面下方的标签和简介：多数录屏里没有滚动到那里，要区分"没看到"和"确实没有"。

**要推出的列**：
- **D、F**：录屏里看到的标题和地址，和 C、E 比较，一样就照抄，不一样就填录屏里的；地址栏没显示的，F 填"未显示"。
- **R（是否完整播放）**：拿"开始播放到录屏结束"的内容时长，和视频本身的时长比；有读数的，就用读数判断有没有跳转。拿不准的标黄。

**素材**：用示例 13–17 号和试下载的 5 个开发。**不得读取同事已填的结论列**。

## 3. 交付

- `manifest.csv`，以及总大小；
- 5 个试下载文件的校验结果；
- 5 条录屏的台账样例：D、F、L、R，以及读到的标签和简介可见性。

## 5. 补充（Owner，2026-09-28，WO-102 验收后追加）

1. **网页台账的输出**按 `pilot/tsxk/config.py` 开头写的接口来，Claude Code 的时间线和分类直接读取。至少要有：
   - 播放区间，用录屏秒数表示；
   - 播放器读数；
   - 标签区、简介区是否可见，以及读到的文字；
   - 地址栏 BV、网页标题、视频选集。
2. **另下 30 行检验集的证据**：名单在 `cases/tsxk_check30.csv`，来自"张亚晨"sheet。下载办法和 §1 相同，存到 `/Volumes/Element/ip-video-matcher-field-test/evidence_check30/`。所有者已同意自动下载（D-057）。
3. **不得读取"张亚晨"的结论列**，也就是 Q–AC。

## 6. 交接补充（Owner，2026-09-28，D-059）

1. **先合并 main**：先提交 `pilot/ezcun/fetch.py` 里未提交的修改，再 `git merge main`，不要 rebase。main 上已经有：
   - WO-102 的 `pilot/tsxk/config.py`、`cases/tsxk.yaml`、`cases/tsxk_check30.csv`；
   - 本工单的追加内容。
2. **数据放共用目录**：本工作树的 `workspace/tsxk` 已经是软链接，指向 `/Users/lestersun/IP-Video-Matcher/workspace/tsxk`。manifest、下载记录、元数据、Chrome 配置都在那里。不要改回本地目录。
3. **下载**：
   - **先试 3 条**：用 7820、7822、7823 试普通 HTTP 下载（D-059 ④）。
     - 签名直链从分享页自己的响应里取。
     - 请求只加 Range 头，写到 `.part`，中断了就接着续。
     - 大小和 md5 都核对无误，再改成正式文件名。
   - **3 条都通过**：用这种方式把其余各条下完。
   - **直链不接受普通下载**：不要改请求头，退回浏览器下载，并修好"浏览器中途关闭"的问题。
   - 一次只下一个文件；分享页之间仍然间隔 ≥ 5 秒；全量只跑一份。
   - `downloads.csv` 里的旧失败记录不算数，以外置盘上核对过的文件为准。
   - Chrome 下载目录里残留的 `.crdownload` 是下到一半的文件，可以删掉。
4. **检验集 30 条**：
   - 分享链接用 `cases/tsxk_check30.csv` 的"取证视频链接"列，不必打开"张亚晨"表。
   - 元数据另写一份 `manifest_check30.csv`。
   - 文件存到 `evidence_check30/<序号>.mp4`。
5. **台账输出**：
   - 正式行和检验集都写到 `workspace/tsxk/webledger/<序号>.json`，两边的序号不重叠。
   - 示例录屏写到 `workspace/tsxk/webledger/samples/<录像名>.json`，例如 `13.json`。
   - 示例 5 条的 R 目前全是"待核"，因为没读到悬停时才出现的读数。要补一个办法：用播放区间的长度，对比视频本身的时长。两种办法都拿不准的，才标黄。
   - 下载完成后放后台跑台账：先跑检验集 30 条，再跑 400 条。

## 4. 不做

- 不绕过网站防护；
- 不改原表；
- 不上传视频；
- 不 push。
