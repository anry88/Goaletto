"""Integer recovery in real elapsed seconds; no clock or persistence."""

POLICY = "recovery-v1"
DAY = 86400


def daily_recovery(stamina: int, age: int, physio_skill: int) -> int:
    if not (1 <= stamina <= 20 and 17 <= age <= 38 and 0 <= physio_skill <= 20):
        raise ValueError("Invalid recovery characteristics")
    stamina_factor = 10000 + (stamina - 9) * 300
    age_factor = 10000 - max(0, age - 27) * 200 + max(0, 25 - age) * 50
    return (1800 + 100 * physio_skill) * stamina_factor * age_factor // 10000**2


def recover(energy: int, remainder: int, seconds: int, rate: int) -> tuple[int, int]:
    if not (0 <= energy <= 10000 and 0 <= remainder < DAY and seconds >= 0 and rate > 0):
        raise ValueError("Invalid recovery interval")
    gained, fraction = divmod(remainder + seconds * rate, DAY)
    result = min(10000, energy + gained)
    return result, fraction if result < 10000 else 0


def ready_in(energy: int, remainder: int, rate: int) -> int:
    if not 0 <= energy <= 10000 or rate <= 0:
        raise ValueError("Invalid energy forecast")
    return max(0, ((10000 - energy) * DAY - remainder + rate - 1) // rate)
