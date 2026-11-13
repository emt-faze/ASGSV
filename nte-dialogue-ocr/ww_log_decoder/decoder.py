#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
鸣潮日志解密工具 (Wuthering Waves Log Decryptor)

把游戏日志 Client.log 复制到本程序所在目录，然后直接运行本程序
（或把日志文件直接拖到本程序图标上），即可得到明文日志 decrypted_Client.log。

命令行用法:
    python decrypt_log.py                  # 自动查找同目录下的 Client.log
    python decrypt_log.py <日志文件路径>     # 手动指定日志文件

游戏日志原始位置:
    <游戏安装目录>\\Wuthering Waves Game\\Client\\Saved\\Logs\\Client.log
"""

import sys
from pathlib import Path

LOG_MAGIC = b"\xa5\xef\xa5"
LOG_START_MARKER = "Log file open"
VERSION = "1.0.0"


def ensure_utf8_console() -> None:
    """让中文 Windows 控制台也能正常打印 emoji，避免编码报错。"""
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass


def app_dir() -> Path:
    """程序所在目录：打包成 exe 后即 exe 所在目录；以脚本运行时即脚本所在目录。"""
    if getattr(sys, "frozen", False):  # PyInstaller 打包
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent


def decrypt_client_log(data: bytes) -> bytes:
    """解密客户端日志：跳过前 3 字节魔数头，其余字节按奇偶分别 XOR 0xA5 / 0xEF。"""
    if len(data) < 3:
        return data
    out = bytearray(len(data) - 3)
    for i, b in enumerate(data[3:]):
        out[i] = b ^ (0xA5 if b % 2 == 1 else 0xEF)
    return bytes(out)


def is_log_encrypted(data: bytes) -> bool:
    """判断日志是否加密：带魔数头，或首字节为 0 且解密后可识别为日志。"""
    if data[:3] == LOG_MAGIC:
        return True
    if len(data) > 3 and data[0] == 0:
        return decrypt_client_log(data).decode("utf-8", errors="ignore").startswith(LOG_START_MARKER)
    return False


def find_input_path() -> Path | None:
    """定位日志文件：优先命令行参数（支持拖拽），其次程序所在目录下的 Client.log。"""
    if len(sys.argv) > 1:
        p = Path(sys.argv[1])
        if p.is_file():
            return p
        print(f"错误: 指定的文件不存在 -> {p}")
        return None
    for name in ("Client.log", "client.log"):
        p = app_dir() / name
        if p.is_file():
            return p
    return None


def wait_exit() -> None:
    """交互式运行结束时暂停，避免双击 exe 后窗口一闪而过。"""
    try:
        if sys.stdin is not None and sys.stdin.isatty():
            input("\n按回车键退出...")
    except Exception:
        pass


def main() -> None:
    ensure_utf8_console()
    print("=" * 56)
    print("  鸣潮日志解密工具  Wuthering Waves Log Decryptor")
    print(f"  版本: {VERSION}")
    print("=" * 56)

    input_path = find_input_path()
    if input_path is None:
        print()
        print("未找到 Client.log，请把日志文件放到本程序所在文件夹后重试：")
        print(f"  程序目录: {app_dir()}")
        print("也可以把日志文件直接拖到本程序图标上。")
        print()
        print("游戏日志原始位置示例：")
        print("  E:\\Wuthering Waves\\Wuthering Waves Game\\Client\\Saved\\Logs\\Client.log")
        wait_exit()
        sys.exit(1)

    try:
        data = input_path.read_bytes()
        print(f"\n读取文件: {input_path.name}")
        print(f"文件大小: {len(data):,} 字节")

        if is_log_encrypted(data):
            print("🔓 检测到加密日志，正在解密...")
            decrypted = decrypt_client_log(data)
        else:
            print("📄 文件已是明文日志，无需解密")
            decrypted = data

        output_path = input_path.with_name(f"decrypted_{input_path.name}")
        output_path.write_bytes(decrypted)
        print("✅ 解密完成！")
        print(f"   输出文件 -> {output_path}")
        print(f"   输出大小 -> {len(decrypted):,} 字节")

        text = decrypted.decode("utf-8", errors="ignore")
        if LOG_START_MARKER in text:
            print("✅ 校验通过：明文日志开头包含 “Log file open”")
        else:
            print("⚠️ 提示：解密结果开头未见 “Log file open”，请确认日志文件格式是否为本工具支持的加密日志")
    except Exception as e:
        print(f"❌ 发生错误: {e}")

    wait_exit()


if __name__ == "__main__":
    main()
