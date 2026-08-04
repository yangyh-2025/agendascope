# mihomo(Clash Meta)境外代理部署

## 背景

阿里云大陆服务器对境外 HTTP/HTTPS 出站默认阻断/限速,导致 56/125 个境外媒体源全部 failed 无法采集。本方案在服务器上跑 mihomo(Clash.Meta 内核)作为本地代理,采集 worker 通过它访问境外。

## 架构

```
采集 worker (docker 容器)
   ↓ HTTPS_PROXY=http://172.19.0.1:7890
mihomo (宿主机 systemd 服务,7890 端口)
   ↓ VLESS/Trojan/Hysteria2 等协议
机场节点(订阅 m.mofa9.xyz)
   ↓
境外媒体源 (BBC/VOA/NYT/...)
```

## 部署步骤(已在生产完成)

1. **下载 mihomo 内核**:
   ```bash
   # 服务器访问 GitHub 慢,建议本机代理下后 scp 上传
   curl -sL -o mihomo.gz "https://github.com/MetaCubeX/mihomo/releases/download/v1.19.29/mihomo-linux-amd64-compatible-v1.19.29.gz"
   gunzip -c mihomo.gz > /opt/mihomo/mihomo
   chmod +x /opt/mihomo/mihomo
   ```

2. **下载 GeoIP/Geosite 数据库**(规则匹配 CN 直连 / 境外走代理):
   ```bash
   curl -sL -o /etc/mihomo/geoip.metadb "https://github.com/MetaCubeX/meta-rules-dat/releases/download/latest/geoip.metadb"
   curl -sL -o /etc/mihomo/geosite.dat  "https://github.com/MetaCubeX/meta-rules-dat/releases/download/latest/geosite.dat"
   ```

3. **拉 Clash 订阅配置**:
   ```bash
   # UA 必须是 ClashMetaForAndroid,否则返回空
   curl -sL -A "ClashMetaForAndroid/2.11.7" \
     -o /etc/mihomo/config.yaml \
     "http://m.mofa9.xyz/s/subscribe/sub/cla/<your-token>"
   # 验证配置
   /opt/mihomo/mihomo -d /etc/mihomo -f /etc/mihomo/config.yaml -t
   ```

4. **systemd 服务** `/etc/systemd/system/mihomo.service`:
   ```ini
   [Unit]
   Description=mihomo (Clash Meta) Proxy Daemon
   After=network.target

   [Service]
   Type=simple
   User=root
   WorkingDirectory=/etc/mihomo
   ExecStart=/opt/mihomo/mihomo -d /etc/mihomo -f /etc/mihomo/config.yaml
   Restart=on-failure
   RestartSec=5
   LimitNOFILE=100000
   MemoryMax=200M

   [Install]
   WantedBy=multi-user.target
   ```
   ```bash
   systemctl daemon-reload
   systemctl enable --now mihomo
   ```

5. **采集 worker 走代理**(`deploy/compose.deploy.yml`):
   ```yaml
   worker:
     environment:
       GLOBAL_SITE_PROXY: http://172.19.0.1:7890   # docker 网桥网关 = 宿主机
       CN_SITE_PROXY: ""                            # CN 源保持直连
   ```
   `172.19.0.1` 是 `docker network inspect` 查到的默认网桥网关,容器经它访问宿主机的 7890 端口。

6. **订阅 12h 自动更新** `/opt/mihomo/update_subscription.sh`:
   - cron `30 3,15 * * *` 每天 03:30 / 15:30 跑
   - 拉新订阅 → mihomo -t 校验 → 通过 mihomo API 热重载(不重启服务)
   - 失败时降级 `systemctl restart mihomo`

## 验证

```bash
# 从容器内测代理
docker exec agendascope-worker-1 python -c "
import requests
proxies = {'http': 'http://172.19.0.1:7890', 'https': 'http://172.19.0.1:7890'}
r = requests.get('https://feeds.bbci.co.uk/news/world/rss.xml', proxies=proxies, timeout=15)
print(r.status_code, len(r.content))
"
# 期望: 200 21107
```

## 注意事项

- **UA 必须 `ClashMetaForAndroid`** — 机场对 Clash/ClashX/默认 UA 返回空响应
- **mihomo 内存 ~60MB** — MemoryMax 200M 保护 2G 服务器
- **GDELT 仍 429** — GDELT 服务端限流,与代理无关;RSS 源走代理正常
- **规则 GEOIP,CN,DIRECT** — mihomo 默认 CN 流量直连不走代理,境外走代理
- **本机代理不要写** `127.0.0.1:7890` 到 docker 容器,容器内的 127.0.0.1 是容器自己,必须用网桥网关 `172.19.0.1`
