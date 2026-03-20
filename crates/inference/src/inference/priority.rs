use std::collections::VecDeque;
use std::task::{Context, Poll, Waker};

#[derive(Debug, Clone, Copy, PartialEq, Eq, PartialOrd, Ord)]
pub enum Priority {
    HumanInteractive,
    ChainReply,
    Worker,
    Autonomous,
    Background,
}

/// Minimal placeholder for the real priority semaphore described in the LLD.
pub struct PrioritySemaphore {
    capacity: usize,
    queue: VecDeque<Priority>,
    waker: Option<Waker>,
}

impl PrioritySemaphore {
    pub fn new(capacity: usize) -> Self {
        Self {
            capacity,
            queue: VecDeque::new(),
            waker: None,
        }
    }

    pub fn acquire(&mut self, priority: Priority) {
        self.queue.push_back(priority);
    }

    pub fn release(&mut self) {
        self.queue.pop_front();
        if let Some(waker) = self.waker.take() {
            waker.wake();
        }
    }

    pub fn poll_ready(&mut self, cx: &mut Context<'_>) -> Poll<()> {
        if self.queue.len() < self.capacity {
            Poll::Ready(())
        } else {
            self.waker = Some(cx.waker().clone());
            Poll::Pending
        }
    }
}
