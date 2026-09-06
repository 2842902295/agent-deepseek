/**
 * Todo 截止时间的展示工具：按本地自然日（calendar day）划桶。
 *
 * 之前的实现用 `diff < 86400000` 这种滚动 24h 窗口判定「今日 / 明天 / 后天」，
 * 跨夜后会错位（晚 11 点看明早 9 点被划到「今日」）。这里所有判断都基于
 * `Date#setHours(0,0,0,0)` 取得的当日 00:00 时间戳，与日历视觉一致。
 */

export type TodoDayBucket =
  | 'overdue'      // 截止时间已过
  | 'today'        // 当日
  | 'tomorrow'     // 次日
  | 'day-after'    // 后天
  | 'this-week'    // 3~6 天后
  | 'later';       // 一周以上

export interface TodoTiming {
  /** 自然日相对差：负数=逾期 N 天，0=今天，1=明天，… */
  dayDelta: number;
  bucket: TodoDayBucket;
  /** 距离当前还有多少小时（向下取整，逾期为负） */
  hoursDelta: number;
  date: Date;
}

/** 把毫秒戳所在那天的 00:00:00 时间戳取出（本地时区）。 */
function startOfDay(ts: number): number {
  const d = new Date(ts);
  d.setHours(0, 0, 0, 0);
  return d.getTime();
}

/**
 * 计算一个待办的自然日分桶 + 相对差。
 *
 * @param dueMs 截止时间的毫秒戳
 * @param nowMs 当前时间毫秒戳，默认 Date.now()
 */
export function getTodoTiming(dueMs: number, nowMs: number = Date.now()): TodoTiming {
  const todayStart = startOfDay(nowMs);
  const dueStart = startOfDay(dueMs);
  const dayDelta = Math.round((dueStart - todayStart) / 86_400_000);
  const hoursDelta = Math.floor((dueMs - nowMs) / 3_600_000);
  const date = new Date(dueMs);

  let bucket: TodoDayBucket;
  if (dueMs < nowMs) {
    bucket = 'overdue';
  } else if (dayDelta === 0) {
    bucket = 'today';
  } else if (dayDelta === 1) {
    bucket = 'tomorrow';
  } else if (dayDelta === 2) {
    bucket = 'day-after';
  } else if (dayDelta < 7) {
    bucket = 'this-week';
  } else {
    bucket = 'later';
  }

  return {dayDelta, bucket, hoursDelta, date};
}

function pad2(n: number): string {
  return String(n).padStart(2, '0');
}

export function fmtTime(d: Date): string {
  return `${pad2(d.getHours())}:${pad2(d.getMinutes())}`;
}

export function fmtMonthDay(d: Date): string {
  return `${d.getMonth() + 1}/${d.getDate()}`;
}

export function fmtFullDate(d: Date): string {
  return `${d.getFullYear()}.${pad2(d.getMonth() + 1)}.${pad2(d.getDate())}`;
}

/** 「明天 / 后天 / N 天后」中文标签（仅用于未来；逾期由调用方处理）。 */
export function dayDeltaLabel(delta: number): string {
  if (delta === 0) return '今日';
  if (delta === 1) return '明天';
  if (delta === 2) return '后天';
  return `${delta} 天后`;
}
