import json
import os
import zlib
from pathlib import Path
from dataclasses import dataclass, asdict
from typing import Optional, List, Dict

@dataclass
class Packet:
    sequence: int
    timestamp: float
    payload: bytes
    checksum: int

    def to_dict(self):
        d = asdict(self)
        d['payload'] = self.payload.hex()
        return d

    @classmethod
    def from_dict(cls, d):
        return cls(
            sequence=d['sequence'],
            timestamp=d['timestamp'],
            payload=bytes.fromhex(d['payload']),
            checksum=d['checksum']
        )

class EventLogger:
    def __init__(self, log_path: Path, state_path: Path):
        self.log_path = log_path
        self.state_path = state_path
        
        # internal state
        self.last_logged_seq = -1  # last sequence number written to log file
        self.buffer: Dict[int, Packet] = {}  # out-of-order packet buffer {seq: Packet}
        self.stats = {
            "received": 0,
            "corrupted": 0,
            "duplicates": 0,
            "written": 0
        }
        
        self._load_state()

    def _load_state(self):
        """load state from state file, handle process crash and restart"""
        if self.state_path.exists():
            try:
                with open(self.state_path, 'r') as f:
                    state = json.load(f)
                    self.last_logged_seq = state.get("last_logged_seq", -1)
                    self.stats = state.get("stats", self.stats)
                    # restore packets in buffer
                    buffer_data = state.get("buffer", {})
                    self.buffer = {int(k): Packet.from_dict(v) for k, v in buffer_data.items()}
            except Exception:
                # if state file is corrupted, start from scratch
                pass

    def _save_state(self):
        """persist current state: this is the key to handling sys.exit(1)"""
        state = {
            "last_logged_seq": self.last_logged_seq,
            "stats": self.stats,
            "buffer": {k: v.to_dict() for k, v in self.buffer.items()}
        }
        # use temporary file to write and then rename, ensure atomicity (Atomic Write)
        temp_path = self.state_path.with_suffix(".tmp")
        with open(temp_path, 'w') as f:
            json.dump(state, f)
        os.replace(temp_path, self.state_path)

    def _verify_checksum(self, packet: Packet) -> bool:
        """verify checksum"""
        return zlib.crc32(packet.payload) == packet.checksum

    def log_packet(self, packet: Packet):
        """handle new arrived packet"""
        self.stats["received"] += 1

        # 1. verify data
        if not self._verify_checksum(packet):
            self.stats["corrupted"] += 1
            self._save_state()
            return

        # 2. deduplicate
        if packet.sequence <= self.last_logged_seq or packet.sequence in self.buffer:
            self.stats["duplicates"] += 1
            self._save_state()
            return

        # 3. put into buffer
        self.buffer[packet.sequence] = packet

        # 4. try to write to log file
        # if current packet is the next one we are waiting for (last + 1), trigger continuous write
        self._drain_buffer()

        # 5. must save state after each packet, prevent memory data loss due to crash
        self._save_state()

    def _drain_buffer(self):
        """write consecutive packets in buffer to log file"""
        write_queue = []
        next_seq = self.last_logged_seq + 1
        
        while next_seq in self.buffer:
            pkt = self.buffer.pop(next_seq)
            write_queue.append(pkt)
            self.last_logged_seq = next_seq
            next_seq += 1
            
        if write_queue:
            self._write_to_log(write_queue)

    def _write_to_log(self, packets: List[Packet]):
        """perform actual disk IO"""
        with open(self.log_path, 'a') as f:
            for p in packets:
                log_entry = {
                    "seq": p.sequence,
                    "ts": p.timestamp,
                    "data": p.payload.hex()
                }
                f.write(json.dumps(log_entry) + "\n")
                self.stats["written"] += 1

    def force_flush(self, max_gap: int = 10):
        """
        gap detection strategy: if packets in buffer are too far from last written packet (exceeds max_gap),
        it means packets in between may be lost, force skip gap and continue writing.
        """
        if not self.buffer:
            return

        min_buffered_seq = min(self.buffer.keys())
        if min_buffered_seq > self.last_logged_seq + max_gap:
            # admit lost packets, update watermark
            self.last_logged_seq = min_buffered_seq - 1
            self._drain_buffer()
            self._save_state()

    def get_stats(self):
        return self.stats
