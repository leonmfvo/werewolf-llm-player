"""対戦サイトとのつなぎ口（このリポジトリでは中身を公開していません）。

think.py / think_free.py は、このクラスを通してサイトのログを読み、発言・投票・夜の能力を行います。
開発時は特定の人狼サイトの画面を読み書きする実装を使っていましたが、
そのサイト専用の自動操作手順になるため、公開版からは外しています。

自分の環境で動かす場合は、下のメソッドを実装してください。
ローカルの模擬村（selfplay.py）は、このファイルを使わずに動きます。
"""
import re


class Zinro:
    """サイト操作のインターフェース。各メソッドの戻り値の形だけを定めています。"""

    def __init__(self, *args, **kwargs):
        raise NotImplementedError(
            "zinro.py は公開版では実装されていません。selfplay.py で模擬の村を動かすか、"
            "使うサイトに合わせてこのクラスを実装してください。")

    # ---- 読み取り ----
    def messages(self):
        """ログ全体を古い順で返す。各要素は dict(from_user, to_user, text)。読めないときは None。"""
        raise NotImplementedError

    def new_messages(self):
        """前回呼んだ後に増えた発言だけを返す（messages() と同じ形のリスト）。"""
        raise NotImplementedError

    def skip_history(self):
        """今あるログを既読扱いにする。"""
        raise NotImplementedError

    def scene(self):
        """'開始前' '夜' '昼' '投票' などのシーン名。読めないときは None。"""
        raise NotImplementedError

    def my_name(self):
        """自分のプレイヤー名。"""
        raise NotImplementedError

    def my_role(self):
        """自分の役職（例: '村人'）。分からなければ None。"""
        raise NotImplementedError

    @staticmethod
    def find_role(msgs, name=None):
        """ログ中の「あなたは【○○】です」から自分の役職を探す（予備）。"""
        for m in reversed(msgs or []):
            if name and m["to_user"] != name:
                continue   # 他人宛ての役職通知は無視
            r = re.search(r"あなたは【(.+?)】", m["text"])
            if r:
                return r.group(1)
        return None

    def time_left(self):
        """残り時間（秒）。読めないときは None。"""
        raise NotImplementedError

    def players(self):
        """プレイヤー一覧。[{"name": str, "alive": bool}] を返す。"""
        raise NotImplementedError

    def is_alive(self, name):
        """name が生存していれば True。"""
        raise NotImplementedError

    def action_choices(self, kind):
        """夜の能力（kind='night'）・投票（kind='vote'）で選べる相手の名前の一覧。"""
        raise NotImplementedError

    # ---- 行動 ----
    def say(self, text):
        """全員（夜は役職チャット）に発言する。"""
        raise NotImplementedError

    def act(self, kind, target):
        """夜の能力（kind='night'）または投票（kind='vote'）で target を選んで実行する。"""
        raise NotImplementedError

    def save_page(self, path):
        """画面を保存する（デバッグ用）。"""
        raise NotImplementedError

    def close(self):
        """接続を閉じる。"""
        pass
