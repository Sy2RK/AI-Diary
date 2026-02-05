# Aily 封面生成 + 写入多维表格（独立脚本）

这个脚本不改现有流程：只负责
1) 调用 Aily OpenAPI（通过 Aily 应用，内部接入 Seedream 等能力）生成封面图；
2) 下载到本地 `covers/`；
3) 上传到飞书 Drive（拿到 `file_token`）；
4) 写入多维表格附件字段 `封面`（按 `唯一键` upsert）。

## 配置

1) 编辑 `aily_cover.yaml`
- `aily.app_id`：你的 Aily 应用 ID（形如 `spring_xxx__c`）
- `aily.biz_user_id`：业务用户 ID（任意稳定字符串）
- `aily.user_access_token`：建议不要落盘，改用环境变量 `AILY_USER_ACCESS_TOKEN`

2) `rednotes.yaml`
- 复用现有 `feishu.app_id/app_secret`、`bitable.app_token/table_id`

## 运行

小红书（XHS）：
```powershell
python aily_cover_sync.py xhs --run-dir outputs/rednotes/20260130-2 -c rednotes.yaml --aily-config aily_cover.yaml
```

公众号（WeChat）：
```powershell
python aily_cover_sync.py wechat --stage2 outputs/wechat/20260127/20260127_stage2.json -c rednotes.yaml --aily-config aily_cover.yaml
```

可选参数：
- `--limit 5`：只跑前 5 条
- `--force`：覆盖已有 `covers/<unique_key>.png`
- `--verbose`：打印更多 Aily run 状态信息

## 推送是否可复用该生成图片 API？

可以：推送到 IM 的关键是拿到 `image_key`。
流程是：Aily 生成图片 URL → 下载到本地 → 调用 `Tools.feishu_utils.upload_image()` → `send_post()` 发富文本。

