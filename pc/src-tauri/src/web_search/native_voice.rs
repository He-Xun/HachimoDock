//! The conversation stays on the configured model's official endpoint.
use super::*;

pub(crate) fn route(cfg: &LlmConfig) -> Option<Provider> {
    let kind = provider(cfg)?;
    (cfg.search.mode != Mode::DeepSeek && !cfg.api_key.trim().is_empty()).then_some(kind)
}

fn native_request(cfg: &LlmConfig, kind: Provider, messages: Value) -> (String, Value) {
    if kind == Provider::DeepSeek {
        if cfg.model == "deepseek-v4-pro" {
            return (
                "https://api.deepseek.com/v1/responses".into(),
                json!({
                    "model":cfg.model,"input":messages,"tools":[{"type":"web_search"}],"stream":true
                }),
            );
        }
        return (
            cfg.completions_url(),
            json!({"model":cfg.model,"messages":messages,
            "tools":[{"type":"deepseek_web_search"}],"thinking":{"type":"disabled"},"stream":true}),
        );
    }
    let (url, mut body) = request(kind, cfg, "");
    body[if kind == Provider::Doubao {
        "input"
    } else {
        "messages"
    }] = messages;
    body["stream"] = true.into();
    (url.into(), body)
}

#[derive(Default)]
struct Output {
    sources: Vec<Source>,
    text: String,
    verified_text_end: usize,
    tool_markup: bool,
    delivered: usize,
    complete: bool,
}

impl Output {
    // Some models emit an XML tool invocation as ordinary assistant text.
    // Hold a partial opening tag across chunks so it never reaches subtitles or TTS.
    fn append_text(&mut self, delta: &str) {
        if self.tool_markup {
            return;
        }
        self.text.push_str(delta);
        const MARKERS: [&str; 3] = ["<tool_call", "<function=", "<parameter="];
        let pending = &self.text[self.verified_text_end..];
        for (offset, _) in pending.match_indices('<') {
            let tail = &pending[offset..];
            if MARKERS.iter().any(|marker| tail.starts_with(marker)) {
                self.verified_text_end += offset;
                self.text.truncate(self.verified_text_end);
                self.tool_markup = true;
                return;
            }
            if MARKERS.iter().any(|marker| marker.starts_with(tail)) {
                self.verified_text_end += offset;
                return;
            }
        }
        self.verified_text_end = self.text.len();
    }
    fn event(&mut self, event: &Value, emit: &mut impl FnMut(&str)) -> Result<(), String> {
        if !event["error"].is_null()
            || matches!(
                event["type"].as_str(),
                Some("error" | "response.failed" | "response.incomplete")
            )
            || matches!(
                event
                    .pointer("/choices/0/finish_reason")
                    .and_then(Value::as_str),
                Some("length" | "content_filter" | "tool_calls")
            )
        {
            return Err("联网回答未完整生成，请重试".into());
        }
        collect_sources(event, 0, &mut self.sources);
        let delta = if event["type"] == "response.output_text.delta" {
            event["delta"].as_str()
        } else {
            event
                .pointer("/choices/0/delta/content")
                .and_then(Value::as_str)
        };
        if let Some(delta) = delta {
            self.append_text(delta);
        }
        if !self.sources.is_empty() && self.verified_text_end > self.delivered {
            emit(&self.text[self.delivered..self.verified_text_end]);
            self.delivered = self.verified_text_end;
        }
        self.complete |= event["type"] == "response.completed"
            || event
                .pointer("/choices/0/finish_reason")
                .is_some_and(|v| v == "stop");
        Ok(())
    }
}

pub(crate) async fn answer(
    cfg: &LlmConfig,
    kind: Provider,
    query: &str,
    messages: Value,
    mut emit: impl FnMut(&str),
) -> Result<SearchResult, String> {
    check_query(cfg, query)?;
    let (url, body) = native_request(cfg, kind, messages);
    let client = crate::llm_network::client(DEADLINE)?;
    let response = authorize(client.post(&url).json(&body), &url, cfg.api_key.trim())
        .send()
        .await
        .map_err(|e| crate::llm_network::connection_error(&e))?;
    if !response.status().is_success() {
        return Err(format!(
            "{}原生联网请求失败（HTTP {}），请检查模型、联网工具权限与额度",
            kind.label(),
            response.status().as_u16()
        ));
    }
    let mut stream = response.bytes_stream();
    let mut output = Output::default();
    let mut buffer = Vec::new();
    let mut total = 0;
    while let Some(chunk) = stream.next().await {
        let chunk = chunk.map_err(|_| "联网回答接收中断")?;
        total += chunk.len();
        if total > MAX_RESPONSE {
            return Err("联网回答超过大小限制".into());
        }
        buffer.extend_from_slice(&chunk);
        let mut consumed = 0;
        for line in buffer.split_inclusive(|b| *b == b'\n') {
            if !line.ends_with(b"\n") {
                break;
            }
            consumed += line.len();
            let Some(data) = line.strip_prefix(b"data:") else {
                continue;
            };
            let data = std::str::from_utf8(data)
                .map_err(|_| "联网回答编码无效")?
                .trim();
            if data == "[DONE]" || data.is_empty() {
                continue;
            }
            let event = serde_json::from_str(data).map_err(|_| "联网回答事件格式异常")?;
            output.event(&event, &mut emit)?;
        }
        buffer.drain(..consumed);
        if output.complete {
            break;
        }
    }
    output.text.truncate(output.verified_text_end);
    if !output.complete || output.sources.is_empty() || output.text.trim().is_empty() {
        return Err("未收到带可核验来源的完整联网回答".into());
    }
    Ok(SearchResult {
        provider: kind.label().into(),
        summary: output.text,
        sources: output.sources,
        retrieved_at: chrono::Utc::now().to_rfc3339(),
    })
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn native_models_get_the_conversation_and_only_their_server_tool() {
        let messages = json!([{"role":"system","content":"宠物人设"},{"role":"user","content":"上一轮"},{"role":"user","content":"追问"}]);
        for (id, field, tool) in [
            ("doubao", "input", "web_search"),
            ("mimo", "messages", "web_search"),
            ("deepseek", "messages", "deepseek_web_search"),
        ] {
            let preset = crate::voice_chat_settings::llm_preset(id).unwrap();
            let mut cfg = LlmConfig {
                base_url: preset.base_url.into(),
                model: preset.model.into(),
                web_search: true,
                api_key: "fixture".into(),
                search: Default::default(),
            };
            let (url, body) = native_request(&cfg, route(&cfg).unwrap(), messages.clone());
            assert_ne!(url, DEEPSEEK_SEARCH_URL);
            assert_eq!(body[field], messages);
            assert_eq!(body["model"], cfg.model);
            assert_eq!(body["stream"], true);
            assert_eq!(body["tools"].as_array().unwrap().len(), 1);
            assert_eq!(body["tools"][0]["type"], tool);
            cfg.search.mode = Mode::DeepSeek;
            assert!(route(&cfg).is_none());
        }
    }

    #[test]
    fn streaming_only_speaks_answer_text_after_public_evidence() {
        for citation in [
            json!({"choices":[{"delta":{"annotations":[{"type":"url_citation","url":"https://example.org/article","summary":"检索摘要"}]}}]}),
            json!({"choices":[{"delta":{"server_tool_calls":[{"calls":[{"sources":[{"type":"url","url":"https://example.org/article"}]}]}]}}]}),
            json!({"type":"response.output_item.done","item":{"type":"web_search_call","results":[{"type":"web_search_result","url":"https://example.org/article"}]}}),
        ] {
            let mut out = Output::default();
            let mut spoken = String::new();
            out.event(
                &json!({"choices":[{"delta":{"content":"船长，","reasoning_content":"内部推理"}}]}),
                &mut |s| spoken += s,
            )
            .unwrap();
            assert!(spoken.is_empty());
            out.event(&citation, &mut |s| spoken += s).unwrap();
            assert_eq!(spoken, "船长，");
            out.event(
                &json!({"type":"response.output_text.delta","delta":"有消息了。"}),
                &mut |s| spoken += s,
            )
            .unwrap();
            out.event(&json!({"type":"response.completed"}), &mut |s| spoken += s)
                .unwrap();
            assert!(out.complete);
            assert_eq!(out.sources.len(), 1);
            assert_eq!(spoken, "船长，有消息了。");
        }
    }

    #[test]
    fn split_model_tool_markup_never_reaches_speech_or_saved_answer() {
        let mut output = Output::default();
        let mut spoken = String::new();
        for part in [
            "船长，2 <",
            " 3。<",
            "tool_",
            "call><function=web_search>",
            "不应朗读",
        ] {
            output.event(&json!({"choices":[{"delta":{"annotations":[{"type":"url_citation","url":"https://example.org/article"}],"content":part}}]}), &mut |s|spoken+=s).unwrap();
        }
        assert_eq!(spoken, "船长，2 < 3。");
        assert_eq!(output.text, spoken);
    }

    #[test]
    fn failed_and_truncated_streams_cannot_complete() {
        for event in [
            json!({"type":"response.incomplete"}),
            json!({"error":{"code":"denied"}}),
            json!({"choices":[{"finish_reason":"length"}]}),
        ] {
            let mut out = Output::default();
            assert!(out
                .event(&event, &mut |_| panic!("unexpected speech"))
                .is_err());
            assert!(!out.complete);
        }
    }
}
