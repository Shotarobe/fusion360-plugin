# Shotarobe Fusion 360 マーケットプレイス

Codex (および Claude Code) 用のプラグインを配布するためのマーケットプレイスです。

## 収録プラグイン

| プラグイン | バージョン | カテゴリ | 概要 |
|---|---|---|---|
| [`fusion-360`](plugins/fusion-360) | `0.3.0` | Productivity | Autodesk Fusion 360 をローカル Python API ブリッジ経由で Codex から操作 |

## インストール (Codex)

```bash
codex plugin marketplace add https://github.com/Shotarobe/fusion-360.git
```

または Codex の GUI から「マーケットプレイスを追加」を開き、次のように入力します:

- ソース: `https://github.com/Shotarobe/fusion-360.git`
- Git ref: `main`
- スパースパス: 空欄

マーケットプレイス追加後、`fusion-360` プラグインをインストールしてください。

## インストール (Claude Code)

```bash
claude plugin marketplace add https://github.com/Shotarobe/fusion-360.git
```

## マーケットプレイスのレイアウト

```
.
├── .agents/plugins/marketplace.json     # Codex 用マニフェスト
├── .claude-plugin/marketplace.json       # Claude Code 用マニフェスト
└── plugins/
    └── fusion-360/                       # プラグイン本体
        ├── .codex-plugin/plugin.json
        ├── .mcp.json
        ├── README.md
        ├── scripts/
        └── skills/
```

各プラグインの詳細はそのフォルダの `README.md` を参照してください。
