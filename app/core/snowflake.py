import threading
import time


class SnowflakeGenerator:
    """Twitter Snowflake style 64-bit id generator.

    Layout:
    - 41 bits timestamp in milliseconds
    - 5 bits datacenter id
    - 5 bits worker id
    - 12 bits sequence
    """

    epoch_ms = 1704067200000  # 2024-01-01 00:00:00 UTC

    def __init__(self, worker_id: int, datacenter_id: int) -> None:
        """Create a snowflake generator.

        Args:
            worker_id: Worker id in range 0-31.
            datacenter_id: Datacenter id in range 0-31.
        """
        if not 0 <= worker_id <= 31:
            raise ValueError("worker_id must be between 0 and 31")
        if not 0 <= datacenter_id <= 31:
            raise ValueError("datacenter_id must be between 0 and 31")
        self.worker_id = worker_id
        self.datacenter_id = datacenter_id
        self.sequence = 0
        self.last_timestamp = -1
        self._lock = threading.Lock()

    def next_id(self) -> int:
        """Generate the next globally unique integer id."""
        with self._lock:
            timestamp = self._timestamp()
            if timestamp < self.last_timestamp:
                raise RuntimeError("Clock moved backwards")
            if timestamp == self.last_timestamp:
                self.sequence = (self.sequence + 1) & 0xFFF
                if self.sequence == 0:
                    timestamp = self._wait_next_millis(timestamp)
            else:
                self.sequence = 0
            self.last_timestamp = timestamp
            return (
                ((timestamp - self.epoch_ms) << 22)
                | (self.datacenter_id << 17)
                | (self.worker_id << 12)
                | self.sequence
            )

    def next_order_no(self, prefix: str = "XO") -> str:
        """Generate a string order number with a business prefix."""
        return f"{prefix}{self.next_id()}"

    @staticmethod
    def _timestamp() -> int:
        """Return current timestamp in milliseconds."""
        return int(time.time() * 1000)

    def _wait_next_millis(self, current_timestamp: int) -> int:
        """Block until the next millisecond."""
        timestamp = self._timestamp()
        while timestamp <= current_timestamp:
            timestamp = self._timestamp()
        return timestamp

