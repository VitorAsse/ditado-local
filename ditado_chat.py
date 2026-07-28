import customtkinter as ctk

from ditado_ai import normalize_agent_conversation
from ditado_theme import APP_COLORS, app_font


class AgentChatWindow:
    def __init__(self, root, conversation, on_send, on_copy):
        normalized = normalize_agent_conversation(conversation)
        if normalized is None:
            raise ValueError("Esta conversa não tem contexto válido para continuar.")

        self.conversation = normalized
        self.on_send = on_send
        self.on_copy = on_copy
        self.loading = False
        self.closed = False

        self.window = ctk.CTkToplevel(root)
        self.window.title("Continuar com o agente")
        self.window.geometry("620x650")
        self.window.minsize(480, 540)
        self.window.configure(fg_color=APP_COLORS["background"])
        self.window.protocol("WM_DELETE_WINDOW", self.close)
        self.window.transient(root)

        shell = ctk.CTkFrame(self.window, fg_color="transparent")
        shell.pack(fill="both", expand=True, padx=20, pady=20)

        header = ctk.CTkFrame(
            shell,
            fg_color=APP_COLORS["surface_deep"],
            corner_radius=18,
            border_width=1,
            border_color=APP_COLORS["border"],
        )
        header.pack(fill="x", pady=(0, 12))
        icon = ctk.CTkLabel(
            header,
            text="AI",
            width=42,
            height=42,
            corner_radius=13,
            fg_color=APP_COLORS["primary_tint"],
            text_color=APP_COLORS["primary"],
            font=app_font(13, "bold"),
        )
        icon.pack(side="left", padx=(16, 12), pady=14)
        title_group = ctk.CTkFrame(header, fg_color="transparent")
        title_group.pack(side="left", fill="x", expand=True, pady=12)
        ctk.CTkLabel(
            title_group,
            text="Continuar com o agente",
            text_color=APP_COLORS["text_strong"],
            font=app_font(18, "bold"),
        ).pack(anchor="w")
        ctk.CTkLabel(
            title_group,
            text="Peça ajustes usando o texto e as respostas desta conversa.",
            text_color=APP_COLORS["text_muted"],
            font=app_font(11),
        ).pack(anchor="w", pady=(2, 0))

        self.messages_frame = ctk.CTkScrollableFrame(
            shell,
            fg_color=APP_COLORS["surface_deep"],
            corner_radius=18,
            border_width=1,
            border_color=APP_COLORS["border"],
        )
        self.messages_frame.pack(fill="both", expand=True)

        composer = ctk.CTkFrame(
            shell,
            fg_color=APP_COLORS["surface"],
            corner_radius=18,
            border_width=1,
            border_color=APP_COLORS["border"],
        )
        composer.pack(fill="x", pady=(12, 0))
        self.input = ctk.CTkTextbox(
            composer,
            height=88,
            corner_radius=12,
            border_width=1,
            border_color=APP_COLORS["border"],
            fg_color=APP_COLORS["surface_muted"],
            text_color=APP_COLORS["text"],
            font=app_font(13),
            wrap="word",
        )
        self.input.pack(fill="x", padx=12, pady=(12, 8))
        self.input.bind("<Control-Return>", self._submit_from_event)

        action_row = ctk.CTkFrame(composer, fg_color="transparent")
        action_row.pack(fill="x", padx=12, pady=(0, 12))
        self.status_label = ctk.CTkLabel(
            action_row,
            text="Ctrl + Enter para enviar",
            text_color=APP_COLORS["text_subtle"],
            font=app_font(10),
        )
        self.status_label.pack(side="left")
        self.copy_button = ctk.CTkButton(
            action_row,
            text="Copiar resposta",
            width=116,
            height=34,
            corner_radius=10,
            fg_color=APP_COLORS["surface_muted"],
            hover_color=APP_COLORS["surface_hover"],
            border_width=1,
            border_color=APP_COLORS["border"],
            text_color=APP_COLORS["text"],
            font=app_font(11, "bold"),
            command=self._copy_latest,
        )
        self.copy_button.pack(side="right", padx=(8, 0))
        self.send_button = ctk.CTkButton(
            action_row,
            text="Enviar ajuste",
            width=112,
            height=34,
            corner_radius=10,
            fg_color=APP_COLORS["accent"],
            hover_color=APP_COLORS["accent_hover"],
            text_color=APP_COLORS["text_strong"],
            font=app_font(11, "bold"),
            command=self.submit,
        )
        self.send_button.pack(side="right")

        self._render_messages()
        self.window.after(100, self._focus_input)

    def _focus_input(self):
        if self.is_open():
            self.window.lift()
            self.input.focus_set()

    def _render_messages(self):
        for child in self.messages_frame.winfo_children():
            child.destroy()
        for message in self.conversation["messages"]:
            is_user = message["role"] == "user"
            row = ctk.CTkFrame(self.messages_frame, fg_color="transparent")
            row.pack(fill="x", padx=10, pady=5)
            bubble = ctk.CTkFrame(
                row,
                fg_color=(
                    APP_COLORS["accent_tint"]
                    if is_user
                    else APP_COLORS["surface_muted"]
                ),
                corner_radius=14,
                border_width=1,
                border_color=(
                    APP_COLORS["accent"]
                    if is_user
                    else APP_COLORS["border"]
                ),
            )
            bubble.pack(
                side="right" if is_user else "left",
                fill="x",
                expand=False,
                padx=(72, 0) if is_user else (0, 72),
            )
            ctk.CTkLabel(
                bubble,
                text="Você" if is_user else "Agente",
                text_color=(
                    APP_COLORS["accent"]
                    if is_user
                    else APP_COLORS["primary"]
                ),
                font=app_font(10, "bold"),
            ).pack(anchor="w", padx=12, pady=(9, 2))
            ctk.CTkLabel(
                bubble,
                text=message["content"],
                text_color=APP_COLORS["text"],
                justify="left",
                anchor="w",
                wraplength=420,
                font=app_font(12),
            ).pack(anchor="w", padx=12, pady=(0, 10))
        self.messages_frame.after(
            60,
            lambda: self.messages_frame._parent_canvas.yview_moveto(1.0)
            if self.is_open()
            else None,
        )

    def _submit_from_event(self, _event):
        self.submit()
        return "break"

    def submit(self):
        if self.loading or not self.is_open():
            return
        instruction = self.input.get("1.0", "end").strip()
        if not instruction:
            self.status_label.configure(
                text="Digite o ajuste antes de enviar.",
                text_color=APP_COLORS["danger"],
            )
            return
        self.set_loading(True)
        self.on_send(instruction)

    def set_loading(self, loading):
        self.loading = bool(loading)
        self.send_button.configure(
            state="disabled" if self.loading else "normal",
            text="Agente respondendo..." if self.loading else "Enviar ajuste",
        )
        self.copy_button.configure(
            state="disabled" if self.loading else "normal"
        )
        self.status_label.configure(
            text=(
                "Mantendo a conversa e aplicando seu ajuste..."
                if self.loading
                else "Ctrl + Enter para enviar"
            ),
            text_color=APP_COLORS["text_subtle"],
        )

    def show_reply(self, conversation):
        normalized = normalize_agent_conversation(conversation)
        if normalized is None or not self.is_open():
            return
        self.conversation = normalized
        self.input.delete("1.0", "end")
        self.set_loading(False)
        self.status_label.configure(
            text="Resposta pronta. Copie quando estiver satisfeito.",
            text_color=APP_COLORS["success"],
        )
        self._render_messages()

    def show_error(self, message):
        if not self.is_open():
            return
        self.set_loading(False)
        self.status_label.configure(
            text=message,
            text_color=APP_COLORS["danger"],
        )

    def _copy_latest(self):
        if not self.conversation["messages"]:
            return
        latest = self.conversation["messages"][-1]
        if latest["role"] != "assistant":
            return
        self.on_copy(latest["content"])
        self.status_label.configure(
            text="Resposta copiada.",
            text_color=APP_COLORS["success"],
        )

    def is_open(self):
        if self.closed:
            return False
        try:
            return bool(self.window.winfo_exists())
        except Exception:
            return False

    def close(self):
        if self.closed:
            return
        self.closed = True
        try:
            self.window.destroy()
        except Exception:
            pass
