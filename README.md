# 動画内容解析ツール

動画ファイルを指定すると、Ollama（ローカルLLM）が複数フレームを見て内容を解析するツールです。

**解析エンジン**: Ollama のみ（ビジョン対応モデル llava 等 ＋ 日本語用モデルで翻訳）

## デザイン（GUI）

配色・余白は [Google Labs stitch-skills の例 `DESIGN.md`](https://github.com/google-labs-code/stitch-skills/blob/main/skills/design-md/examples/DESIGN.md) を参考にしています。リポジトリ内のコピーは `docs/google-labs-stitch-skills-DESIGN.md` を参照してください。

## セットアップ

### Windows

```bash
cd g:\AI_APP\what_is_video
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
```

### Linux / macOS

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

Linux で GUI を使う場合は、Python の tkinter パッケージが別途必要なことがあります。

```bash
sudo apt-get install python3-tk
```

Ollama をインストールし、ビジョン用・日本語用モデルを用意してください。

```bash
# Ollama のインストール（https://ollama.com）
# その後
ollama pull llava
ollama pull qwen2.5:14b
```

必要に応じて `.env` を作成（`copy .env.example .env`）し、`OLLAMA_BASE_URL` や `OLLAMA_JAPANESE_MODEL` を設定します。

Linux / macOS では次のようにコピーします。

```bash
cp .env.example .env
```

## 使い方

### 1. GUI（デスクトップアプリ）

```bash
python gui.py
```

ウィンドウが開いたら「参照」で動画を選択し、「実行」を押します。Ollama で解析され、結果は同じウィンドウ内にフレームごとに表示されます。GUI は Python 標準の tkinter を使用します。

### 2. Web UI

```bash
uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
```

ブラウザで http://localhost:8000 を開き、動画をドラッグ＆ドロップまたは選択して「解析する」を押すと、内容の要約が表示されます。

### 3. CLI（ローカルファイルを指定）

```bash
python cli.py "C:\path\to\your\video.mp4"
```

標準出力に解析結果が表示されます（Ollama が起動している必要があります）。

### 4. API（パス指定）

サーバーから見えるパスで動画を解析する場合（開発・スクリプト用）:

```bash
curl -X POST "http://localhost:8000/api/analyze-path?path=C:/path/to/video.mp4"
```

## 開発・検証

Ollama を使う解析本体はローカルの Ollama 起動とモデル取得が必要ですが、動画フレーム抽出と Web API の基本動作はテストで確認できます。

```bash
python -m compileall -q .
python -m unittest discover -s tests -v
python -m pip check
```

Linux / macOS で `python` がない場合は `python3` を使ってください。

## 動作の流れ

1. 動画から **GUI で選んだ間隔（2〜120秒）** ごとにフレームを抽出（長い動画で増えすぎないよう上限あり）
2. 各フレームを Ollama のビジョンモデル（llava 等）に送り、英語で要約を取得
3. 必要に応じて日本語用モデル（qwen2.5 等）で自然な日本語に翻訳
4. 結果を返却・表示

## Ollama の設定

| 環境変数 | 説明 | 既定値 |
|----------|------|--------|
| `OLLAMA_BASE_URL` | Ollama の URL | `http://localhost:11434` |
| `OLLAMA_MODEL` | ビジョン用モデル（フレーム解析） | `llava` |
| `OLLAMA_JAPANESE_MODEL` | 日本語訳用モデル（英語→自然な日本語） | 未設定なら英語のまま表示 |
| `OLLAMA_SUMMARY_MODEL` | 総評出力用モデル | 未設定なら `OLLAMA_MODEL`、それも未設定なら `llama3.2` |

**Ollama で読みやすい日本語にする（推奨）**  
ビジョンモデル（llava 等）は日本語が苦手なため、次の**二段階**にしています。  
1. ビジョンモデルで**英語**の要約を取得  
2. `OLLAMA_JAPANESE_MODEL` で指定したモデルで、その英語を**自然な日本語**に翻訳  

`.env` に `OLLAMA_JAPANESE_MODEL=qwen2.5:14b` を指定し、`ollama pull qwen2.5:14b` でモデルを入れると、翻訳品質が高くなります。VRAM に余裕があれば `qwen2.5:32b` も選択肢です。翻訳特化なら `7shi/gemma-2-jpn-translate` も利用できます。

### ollama serve のモデル参照先（保存場所）を変える

Ollama がモデルを **保存・読み込みするディレクトリ** をデフォルトから変更するには、**`ollama serve` を実行する前**に環境変数 **`OLLAMA_MODELS`** を設定します。

**Windows（PowerShellでその場だけ変える）**
```powershell
$env:OLLAMA_MODELS="D:\ollama-models"   # 使いたいフォルダパスに変更
ollama serve
```

**Windows（永続的に変える）**
1. 「設定」→「システム」→「バージョン情報」→「システムの詳細設定」→「環境変数」
2. ユーザーまたはシステムの「新規」で、変数名 `OLLAMA_MODELS`、値にフォルダパス（例: `D:\ollama-models`）を設定
3. Ollama を再起動（タスクマネージャーで終了してから `ollama serve` またはアプリで起動）

**Linux / macOS**
```bash
export OLLAMA_MODELS=/path/to/your/models
ollama serve
```

- 指定したフォルダは **作成されていて書き込み可能** である必要があります。
- 変更後は **Ollama の再起動** が必要です。既に別の場所にモデルがある場合は、そのフォルダを指定するか、あらためて `ollama pull <モデル名>` で新しい場所に取得します。
- このアプリが **どのモデル名を使うか** は、上記のとおり `.env` の `OLLAMA_MODEL` と `OLLAMA_JAPANESE_MODEL` で指定します（ollama serve の参照先とは別の設定です）。

### 環境変数が設定されているか確認する

**Windows（PowerShell）**
```powershell
# 1つだけ確認（例: OLLAMA_MODELS）
$env:OLLAMA_MODELS

# 一覧で確認（OLLAMA で始まるものだけ）
Get-ChildItem Env: | Where-Object { $_.Name -like "OLLAMA*" }
```

**Windows（コマンドプロンプト）**
```cmd
echo %OLLAMA_MODELS%
```

**Linux / macOS（ターミナル）**
```bash
echo $OLLAMA_MODELS
env | grep OLLAMA
```

**このアプリが読んでいる値（.env + システム環境変数）**
```bash
cd g:\AI_APP\what_is_video
.venv\Scripts\activate
python -c "from dotenv import load_dotenv; import os; load_dotenv(); print('OLLAMA_MODEL', os.environ.get('OLLAMA_MODEL')); print('OLLAMA_MODELS', os.environ.get('OLLAMA_MODELS')); print('OLLAMA_BASE_URL', os.environ.get('OLLAMA_BASE_URL'))"
```
- `.env` を読み込んだうえで、アプリから見えている値を表示します。何も出ない場合は「未設定」です。
- **永続的に設定した**環境変数は、設定後に開き直したターミナル／再起動したアプリでないと反映されません。

## Ollama を GPU で動かす

Ollama は **対応 GPU があれば自動で GPU を使います**。特別な「GPU モード ON」は不要です。

### NVIDIA GPU（Windows / Linux）

1. **ドライバを入れる**
   - [NVIDIA ドライバ](https://www.nvidia.com/Download/index.aspx) を **531 以上**に更新する。
   - インストール後、PC を再起動する。

2. **Ollama を入れる**
   - [ollama.com/download](https://ollama.com/download) からインストールする（通常の手順で OK）。

3. **GPU が使われているか確認する**
   - 解析中に **タスクマネージャー** → 「パフォーマンス」→「GPU」で使用率が上がっていれば GPU 使用中。
   - またはコマンドで:
     ```bash
     nvidia-smi
     ```
     「Processes」に `ollama` や関連プロセスが出ていれば GPU 使用中。

4. **複数 GPU がある場合**
   - 使う GPU を限定するには、Ollama を起動する**前**に環境変数を設定する:
     ```bash
     # 例: 0 番の GPU だけ使う（Windows PowerShell）
     $env:CUDA_VISIBLE_DEVICES="0"
     ollama serve
     ```
   - **強制的に CPU だけ**で動かしたい場合（GPU を使わない）:
     ```bash
     $env:CUDA_VISIBLE_DEVICES="-1"
     ollama serve
     ```

**対応目安**: Compute Capability 5.0 以上の NVIDIA GPU（GTX 750 以降の多くの GeForce / Quadro 等）。一覧は [NVIDIA CUDA GPUs](https://developer.nvidia.com/cuda-gpus) を参照。

### AMD GPU（Radeon）

- **Windows**: ROCm v6.1 で RX 6800/6900/7600/7700/7800/7900 系などがサポートされています。Ollama は対応していれば自動で GPU を使用します。
- **Linux**: AMD ROCm v7 ドライバが必要です。[AMD ROCm のドキュメント](https://rocm.docs.amd.com/) でインストール手順を確認してください。

### Apple（Mac）

- M1/M2/M3 等の Mac では、Ollama が **Metal** で GPU を自動利用します。追加設定は通常不要です。

### うまく GPU を使わないとき

- ドライバを 531 以上に更新し、再起動する。
- `nvidia-smi` で GPU が認識されているか確認する。
- Ollama を一度終了してから `ollama serve` で起動し直す。
- 省電力や「グラフィックを省電力 GPU に切り替え」している場合は、Ollama を「高パフォーマンス GPU」で動かすように OS 側で設定する。

---

## トラブルシューティング（Ollama）

**「404」や「APIが使用できませんでした」と出る場合**

- Ollama は **指定したモデルが存在しないときも 404** を返します。まず次を試してください。
  1. **ビジョン用モデルを取得**  
     ```bash
     ollama pull llava
     ```
  2. **取得済みモデルを確認**  
     ```bash
     ollama list
     ```
     `llava` が一覧に出ていれば OK。`.env` で `OLLAMA_MODEL` を変えている場合は、その名前で pull してください。
  3. **Ollama を最新版に更新** … [ollama.com/download](https://ollama.com/download) から再インストール（古い版だと `/v1/chat/completions` が無い場合があります）。
  4. **Ollama が起動しているか** … タスクバー/メニューバーのアイコン、または `ollama list` で確認。ブラウザで http://localhost:11434 を開いて表示されれば起動しています。
  5. **別のアプリが 11434 番を使っていないか** を確認。

**フレームが「完了」なのに解析テキストが空（または「完了（本文なし）」）になる場合**

- 例外ではなく **HTTP 200 で `message.content` が空** のときに起きます。
- 想定されること: モデルが空応答を返した、コンテンツに応じた抑制、一時的な不整合など。
- GUI では空のとき **案内文をテキスト欄に表示**し、ラベルを **「完了（本文なし）」** にします。
- 試せること: `OLLAMA_MODEL` の変更、Ollama の更新、同じ動画での再実行。

**明らかな性行為なのに「なし」と出る場合**

- モデル側の**セーフティや自己検閲**で、性的描写を避けて「なし」と答えることがあります。プロンプトで判定ルールを強化済みですが、根本的には**モデル依存**です。
- 試せること: 別のビジョンモデル（制限が緩い・非検閲に近いもの）への変更、`OLLAMA_MODEL` の切り替え、Ollama / モデルのアップデート。

## 注意

- 長い動画ではフレーム数に上限があり、画像抽出間隔は GUI で選択できます（既定は 10 秒）
- Ollama はローカル実行のため利用料はかかりません
