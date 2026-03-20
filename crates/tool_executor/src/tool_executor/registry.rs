use std::collections::HashMap;
use std::sync::Arc;

use super::types::{ExtensionTool, NativeTool};

#[derive(Debug, Default)]
pub struct ToolRegistry {
    native_tools: HashMap<String, Arc<dyn NativeTool>>,
    extension_tools: HashMap<String, Arc<dyn ExtensionTool>>,
}

impl ToolRegistry {
    pub fn register_native<T>(&mut self, tool: Arc<T>)
    where
        T: NativeTool + 'static,
    {
        self.native_tools.insert(tool.name().to_string(), tool);
    }

    pub fn native(&self, name: &str) -> Option<Arc<dyn NativeTool>> {
        self.native_tools.get(name).cloned()
    }
}
