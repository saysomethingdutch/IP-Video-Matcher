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

## 4. 不做

- 不绕过网站防护；
- 不改原表；
- 不上传视频；
- 不 push。
