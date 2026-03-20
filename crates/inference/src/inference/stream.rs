use tokio::sync::mpsc::{self, Receiver};

use super::types::{ContentBlock, InferenceError, RequestId, StopReason, TokenUsage};

#[derive(Debug)]
pub struct StreamHandle {
    pub events: Receiver<StreamEvent>,
    pub request_id: RequestId,
}

impl StreamHandle {
    pub fn new(buffer: usize, request_id: RequestId) -> (mpsc::Sender<StreamEvent>, Self) {
        let (tx, rx) = mpsc::channel(buffer);
        let handle = Self { events: rx, request_id };
        (tx, handle)
    }

    pub async fn recv(&mut self) -> Option<StreamEvent> {
        self.events.recv().await
    }
}

#[derive(Debug)]
pub enum StreamEvent {
    MessageStart,
    ContentBlockStart { block: ContentBlock },
    ContentBlockDelta { block: ContentBlock },
    ContentBlockStop,
    Metadata(TokenUsage),
    MessageStop { reason: StopReason, usage: TokenUsage },
    Error(StreamErrorEvent),
}

#[derive(Debug)]
pub struct StreamErrorEvent {
    pub error: InferenceError,
    pub partial_content: Vec<ContentBlock>,
}
