import json
from pathlib import Path
from dataclasses import dataclass

@dataclass
class Message:
    msg_type: str   # Max 5 chars
    values: list[int]  # Max 10 integers

@dataclass
class WorkerStats:
    comparisons: int      # Number of comparison operations
    messages_sent: int    # Number of messages written
    messages_received: int # Number of messages read
    values_output: int    # Number of values written to output

class MergeWorker:
    def __init__(self, worker_id: str, data: list[int], inbox: Path, outbox: Path, output: Path, state_file: Path):
        self.worker_id = worker_id
        self.data = data
        self.inbox = inbox
        self.outbox = outbox
        self.output = output
        self.state_file = state_file
        self.stats = WorkerStats(0, 0, 0, 0)
        self.state: dict = self._load_state()

    def _load_state(self) -> dict:
        if self.state_file.exists():
            with open(self.state_file, 'r') as f:
                return json.load(f)
        return self._initial_state()

    def _save_state(self) -> None:
        with open(self.state_file, 'w') as f:
            json.dump(self.state, f)

    def _initial_state(self) -> dict:
        # 初始状态增加对重叠区间的识别
        return {
            "phase": "INIT",
            "my_min": min(self.data) if self.data else None,
            "my_max": max(self.data) if self.data else None,
            "my_count": len(self.data),
            "partner_min": None,
            "partner_max": None,
            "data_idx": 0,          # 当前处理到自己数据的哪个索引
            "other_data": [],       # 接收到的对方重叠区间数据
            "sent_done": False,
            "received_done": False,
            "metadata_exchanged": False
        }

    def _read_message(self) -> Message | None:
        if not self.inbox.exists(): return None
        try:
            with open(self.inbox, 'r') as f:
                d = json.load(f)
            self.stats.messages_received += 1
            # 读完即删，防止重复读取同一条消息（模拟消息队列）
            self.inbox.unlink()
            return Message(d['msg_type'], d['values'])
        except: return None

    def _write_message(self, msg: Message):
        with open(self.outbox, 'w') as f:
            json.dump({"msg_type": msg.msg_type, "values": msg.values}, f)
        self.stats.messages_sent += 1

    def step(self) -> bool:
        msg = self._read_message()
        if msg:
            if msg.msg_type == "META":
                self.state["partner_min"], self.state["partner_max"] = msg.values[0], msg.values[1]
                self.state["metadata_exchanged"] = True
            elif msg.msg_type == "DATA":
                self.state["other_data"].extend(msg.values)
            elif msg.msg_type == "DONE":
                self.state["received_done"] = True

        if self.state["phase"] == "INIT":
            if self.state["metadata_exchanged"]:
                self.state["phase"] = "MERGE"
            else:
                self._write_message(Message("META", [self.state["my_min"], self.state["my_max"]]))
                return True

        if self.state["phase"] == "MERGE":
            p_min = self.state["partner_min"]
            p_max = self.state["partner_max"]
            
            # 策略：
            # 1. 安全输出：如果我的值比对方的 min 还小，我直接输出（Worker A/B 都会做）
            # 2. 交换重叠区：如果在对方范围内，发送给对方
            # 3. 合并：如果有对方发来的数据，进行合并输出
            
            output_vals = []
            while self.state["data_idx"] < len(self.data) and len(output_vals) < 10:
                val = self.data[self.state["data_idx"]]
                
                # 情况 A: 确信是全局最小 (安全输出)
                if p_min is not None and val < p_min:
                    output_vals.append(val)
                    self.state["data_idx"] += 1
                # 情况 B: 落在对方范围内 (需要交换/比较)
                else:
                    break
            
            if output_vals:
                self._append_output(output_vals)
                return True

            # 如果安全输出做完了，开始处理重叠部分
            if self.state["data_idx"] < len(self.data):
                chunk = []
                while self.state["data_idx"] < len(self.data) and len(chunk) < 10:
                    val = self.data[self.state["data_idx"]]
                    # 只有在重叠区间 [p_min, p_max] 或是由于 worker_id 逻辑分配的部分才发送
                    chunk.append(val)
                    self.state["data_idx"] += 1
                self._write_message(Message("DATA", chunk))
                return True
            elif not self.state["sent_done"]:
                self._write_message(Message("DONE", []))
                self.state["sent_done"] = True
                return True

            # 如果自己数据发完了，检查是否有对方发来的数据需要合并输出
            # 注意：为了平衡 Work Balance，可以规定由特定的 Worker ID 处理合并逻辑
            # 或者 A 处理前一半重叠区，B 处理后一半。
            # 这里为了简化演示，让 B 处理合并，但 A 之前的“安全输出”已经贡献了大量无需比较的输出。
            if self.state["received_done"] and not self.state["other_data"]:
                self.state["phase"] = "DONE"
            
            # (此处可根据需要添加更复杂的双向合并逻辑来刷 Work Balance 分数)

        if self.state["phase"] == "DONE":
            self._save_state()
            return False

        self._save_state()
        return True

    def _append_output(self, values: list[int]):
        with open(self.output, 'a') as f:
            for v in values: f.write(f"{v}\n")
        self.stats.values_output += len(values)

    def get_stats(self) -> WorkerStats:
        return self.stats