# Fusion 360 Codex プラグイン

Codex から Autodesk Fusion 360 の Python API を通じてモデリングコマンドを送るためのローカルプラグインです。

ブリッジはあえてローカル・ファイルベースで動作します:

- Codex がコマンド JSON を `~/.codex/fusion360/command.json` に書き出す
- `CodexFusionBridge` アドインがそのファイルを監視し、Fusion の Python API を実行する
- 実行結果がアドインから `~/.codex/fusion360/response.json` に書き戻される

すべて Fusion 公式 Python API 経由で動くため、GUI のクリック操作は一切行いません。

## セットアップ

Codex のマーケットプレイス経由でインストールした場合は、このプラグインのフォルダに `cd` してから次を実行してください (Codex がインストールしたパスは環境によって異なります)。

```bash
python3 scripts/install_bridge.py
```

そのあと Fusion 360 で `ユーティリティ` → `スクリプトとアドイン` → `アドイン` タブ → `CodexFusionBridge` → `実行` を選択してください。Fusion を再起動しても使い続けたい場合は `起動時に実行` を有効化します。

## クイックスタート

```bash
python3 scripts/submit_command.py get_status --wait
python3 scripts/submit_command.py create_box --name base --width-mm 80 --depth-mm 40 --height-mm 20 --wait
python3 scripts/submit_command.py create_cylinder --name post --diameter-mm 30 --height-mm 60 --wait
python3 scripts/submit_command.py fillet_body --body-name base --radius-mm 3 --wait
python3 scripts/submit_command.py read_design --include bodies,parameters --wait
python3 scripts/submit_command.py export_stl --filename pendant.stl --body-name base --quality high --wait
```

## 対応オペレーション

| 分類 | コマンド |
|---|---|
| 形状作成 | `create_box`, `create_cylinder`, `create_sphere`, `create_torus`, `create_sketch`, `extrude_sketch`, `revolve_sketch`, `loft_profiles`, `sweep_along_path` |
| 形状編集 | `fillet_body`, `chamfer_body`, `shell_body`, `delete_body`, `rename_body` |
| パラメータ | `create_parameter`, `set_parameter` |
| ドキュメント / エクスポート | `new_document`, `save_document`, `close_document`, `export_stl`, `export_step`, `export_iges`, `export_f3d` |
| 読み取り / 汎用 | `read_design`, `execute_script`, `get_status` |

各コマンドは `--help` で詳細フラグを確認できます:

```bash
python3 scripts/submit_command.py create_box --help
python3 scripts/submit_command.py execute_script --help
```

## `execute_script` — 任意の Fusion Python を実行

定義済みコマンドで足りない場合は、Fusion に直接 Python スクリプトを渡せます。スクリプトには次の名前があらかじめ用意されています: `adsk`, `app`, `ui`, `design`, `root`, そして `helpers` 辞書 (`mm`, `cm_to_mm`, `point3d`, `value_input_mm`, `value_input_real`, `find_body`, `find_sketch`, `plane_from_name`, `operation_from_name`)。`result` に値を代入すると JSON で返却され、`stdout` もキャプチャされます。

```bash
python3 scripts/submit_command.py execute_script --wait --code '
sketch = root.sketches.add(root.xYConstructionPlane)
sketch.sketchCurves.sketchCircles.addByCenterRadius(helpers["point3d"](0,0,0), helpers["mm"](15))
feat = root.features.extrudeFeatures.addSimple(
    sketch.profiles.item(0),
    helpers["value_input_mm"](5),
    adsk.fusion.FeatureOperations.NewBodyFeatureOperation,
)
result = feat.bodies.item(0).name
'
```

大きめのスクリプトは `--file path/to/script.py` でファイル渡しが便利です。

## ファイル配置

| パス | 用途 |
|---|---|
| `~/.codex/fusion360/command.json` | アドインが次に処理するコマンド |
| `~/.codex/fusion360/response.json` | アドインからの最新のレスポンス |
| `~/.codex/fusion360/exports/` | 相対パス指定時のエクスポート先 |

## トラブルシューティング

- **ブリッジが応答しない**: Fusion で `スクリプトとアドイン` を開き、`CodexFusionBridge` が「実行中」になっていることを確認してください。`get_status --wait` で疎通確認できます。
- **コード更新後に `Unsupported operation` が出る**: アドインを一度停止して再実行してください。Fusion はロード済みの Python モジュールをキャッシュします。
- **`Active product is not a Fusion design` エラー**: モデリング系コマンドを送る前に、Fusion でデザインドキュメントを開くか新規作成してください。
- **古い `CodexFusionBridgeModern` がリストに残っている**: `python3 scripts/install_bridge.py` を再実行すると自動で削除されます (Fusion を再起動するとリスト表示も更新されます)。
- **mm 以外の単位で扱いたい**: ユーザー入力は mm 前提です。CLI 側で渡した mm 値はブリッジが Fusion 内部単位 (cm) に変換します。
