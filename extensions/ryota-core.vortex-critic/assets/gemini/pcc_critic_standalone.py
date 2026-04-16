# pcc_critic.py

import argparse
import sys
import os
import json
import logging

# Configure logging
# Default to basic configuration, will be enhanced based on flags
logging.basicConfig(level=logging.WARNING, format='%(levelname)s: %(message)s')
logger = logging.getLogger(__name__)

# UTF-8 encoding for standard output/error
# This handles LANG=C environments
if sys.version_info.major >= 3 and sys.version_info.minor >= 7:
    sys.stdout.reconfigure(encoding='utf-8')
    sys.stderr.reconfigure(encoding='utf-8')

def get_default_config_path():
    """Returns the default path for the configuration file."""
    home_dir = os.path.expanduser("~")
    return os.path.join(home_dir, ".pcc_critic.json")

def load_config(config_path):
    """Loads configuration from a JSON file."""
    if not os.path.exists(config_path):
        logger.info(f"設定ファイルが見つかりません: {config_path}")
        return {}
    try:
        with open(config_path, 'r', encoding='utf-8') as f:
            config = json.load(f)
        logger.info(f"設定ファイルを読み込みました: {config_path}")
        return config
    except json.JSONDecodeError as e:
        logger.error(f"設定ファイルの読み込み中にエラーが発生しました ({config_path}): {e}")
        return {}
    except Exception as e:
        logger.error(f"設定ファイルの読み込み中に予期せぬエラーが発生しました ({config_path}): {e}")
        return {}

def main():
    parser = argparse.ArgumentParser(description="pcc_critic.py: 5 presetsを持つPython CLIツール")

    # CI/CD対応フラグ
    parser.add_argument(
        "--ci-mode",
        action="store_true",
        help="CI/CD環境向けに、カラー出力を無効化し、ログレベルをINFOに設定します。",
    )

    # ログレベル調整
    parser.add_argument(
        "-v", "--verbose",
        action="store_true",
        help="詳細なログ出力を有効にします (INFOレベル)。"
    )
    parser.add_argument(
        "-d", "--debug",
        action="store_true",
        help="デバッグログ出力を有効にします (DEBUGレベル)。"
    )

    # 設定ファイルパス
    parser.add_argument(
        "--config",
        type=str,
        default=get_default_config_path(),
        help=f"設定ファイルのパスを指定します (デフォルト: {get_default_config_path()})。"
    )

    # パイプサイズ制限
    parser.add_argument(
        "--max-output-lines",
        type=int,
        default=0, # 0 means no limit
        help="標準出力に書き出す行数を最大N行に制限します。0の場合、制限なし。"
    )

    # プリセット
    parser.add_argument(
        "preset",
        nargs='?', # Make it optional
        default=None, # Set default to None
        choices=["preset1", "preset2", "preset3", "preset4", "preset5"],
        help="実行するプリセットを選択します。",
    )

    args = parser.parse_args()

    # Configure logging based on arguments
    if args.debug:
        logger.setLevel(logging.DEBUG)
    elif args.verbose:
        logger.setLevel(logging.INFO)
    elif args.ci_mode:
        logger.setLevel(logging.INFO)
        # In CI mode, disable color output if a custom formatter is used later.
        # For basicConfig, this means just setting level and assuming plain text.
        # More advanced color disabling would require custom handlers/formatters.
        logger.info("CI/CDモードが有効です。カラー出力は無効化されます。")

    logger.debug(f"解析された引数: {args}")

    # Load configuration
    config = load_config(args.config)
    logger.debug(f"読み込まれた設定: {config}")

    # Apply config defaults if not overridden by CLI args (simple example)
    # For a real tool, more sophisticated merging might be needed.
    # Here, we'll assume CLI args always take precedence for direct args,
    # but config might hold other values not directly exposed by argparse.
    # Example: If 'default_preset' is in config, and 'preset' is not given via CLI.
    if 'default_preset' in config and args.preset is None:
        args.preset = config['default_preset']
        logger.info(f"設定ファイルからデフォルトプリセット '{args.preset}' を使用します。")

    logger.info(f"選択されたプリセット: {args.preset}")

    # Simulate output with pipe size limit
    output_lines_count = 0
    max_lines = args.max_output_lines

    for i in range(1, 20): # Simulate some output
        if max_lines > 0 and output_lines_count >= max_lines:
            logger.warning(f"最大出力行数 ({max_lines}) に達しました。残りの出力は省略されます。")
            break

        line = f"これは '{args.preset}' の出力ライン {i} です。"
        print(line)
        output_lines_count += 1
        logger.debug(f"出力行数: {output_lines_count}")

    logger.info("処理が完了しました。")

if __name__ == "__main__":
    main()
