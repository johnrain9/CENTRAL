use inference::{StreamEvent, StreamHandle};

pub async fn consume_stream(handle: &mut StreamHandle) -> Vec<inference::ContentBlock> {
    let mut content = Vec::new();
    while let Some(event) = handle.recv().await {
        match event {
            StreamEvent::ContentBlockStart { block }
            | StreamEvent::ContentBlockDelta { block } => content.push(block),
            StreamEvent::Error(err) => {
                tracing::warn!(?err, "stream error");
                break;
            }
            _ => {}
        }
    }
    content
}
