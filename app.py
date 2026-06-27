import streamlit as st
from PIL import Image, ImageOps
import io
import zipfile
import json
import html
from core import process_image, extract_exif, format_exif_text

st.set_page_config(page_title="Exif Framer", page_icon="📸", layout="wide")

# ---------------------------------------------------------------------------
# 定数
# ---------------------------------------------------------------------------
EXIF_FIELDS = ("camera", "lens", "fl", "f", "ss", "iso", "time", "date")

FORMAT_MAP_BASE = {
    "jpg":  ("JPEG", "jpg",  "image/jpeg"),
    "jpeg": ("JPEG", "jpg",  "image/jpeg"),
    "png":  ("PNG",  "png",  "image/png"),
    "tif":  ("TIFF", "tif",  "image/tiff"),
    "tiff": ("TIFF", "tiff", "image/tiff"),
}

DEFAULT_FONTS = {
    "Noto Sans JP (Bold)":    "noto_sans_bold",
    "Noto Sans JP (Regular)": "noto_sans_regular",
    "Roboto (Bold)":          "roboto_bold",
    "Roboto (Regular)":       "roboto_regular",
}

# ---------------------------------------------------------------------------
# ユーティリティ
# ---------------------------------------------------------------------------

def deduplicate_filenames(uploaded_files):
    """同名ファイルに連番（アンダーバー区切り）を付与し、
    (unique_id, uploaded_file) のリストを返す。
    例: ['a.jpg', 'a.jpg', 'b.jpg'] → [('a.jpg', f0), ('a_(2).jpg', f1), ('b.jpg', f2)]
    """
    seen = {}
    result = []
    for f in uploaded_files:
        name = f.name
        if name not in seen:
            seen[name] = 1
            result.append((name, f))
        else:
            seen[name] += 1
            base, ext = name.rsplit(".", 1) if "." in name else (name, "")
            new_id = f"{base}_({seen[name]}).{ext}" if ext else f"{base}_({seen[name]})"
            result.append((new_id, f))
    return result


def resolve_output_format(f_id, heic_to_jpeg):
    """ファイル拡張子から (PIL format, 出力拡張子, MIME type) を返す。"""
    ext = f_id.rsplit(".", 1)[-1].lower()
    heic_entry = ("JPEG", "jpg", "image/jpeg") if heic_to_jpeg else ("HEIF", "heic", "image/heic")
    heif_entry = ("JPEG", "jpg", "image/jpeg") if heic_to_jpeg else ("HEIF", "heif", "image/heif")
    fmt_map = {**FORMAT_MAP_BASE, "heic": heic_entry, "heif": heif_entry}
    return fmt_map.get(ext, ("JPEG", "jpg", "image/jpeg"))


def resolve_font_params(font_choice, font_options, custom_fonts):
    """フォント選択名から (font_family, font_bytes) を返す。"""
    if font_choice.startswith("カスタム: "):
        font_name = font_choice.removeprefix("カスタム: ")
        return "uploaded_custom_font", custom_fonts.get(font_name)
    return font_options.get(font_choice, "noto_sans_bold"), None


def get_exif_bytes(img):
    """Orientation を Normal に設定した Exif バイト列を返す。失敗時は None。"""
    try:
        exif_obj = img.getexif()
        if exif_obj:
            exif_obj[274] = 1  # Orientation = Normal
            return exif_obj.tobytes()
    except Exception:
        pass
    return None


# ---------------------------------------------------------------------------
# セッション状態ヘルパー
# ---------------------------------------------------------------------------
EXIF_SS_KEYS = {field: f"edit_{field}" for field in EXIF_FIELDS}


def _exif_ss_key(field, f_id):
    return f"edit_{field}_{f_id}"


def init_session_state():
    st.session_state.setdefault("processed_results", {})
    st.session_state.setdefault("initialized_exif", {})   # {f_id: True}
    st.session_state.setdefault("custom_fonts", {})


def is_exif_initialized(f_id):
    return f_id in st.session_state["initialized_exif"]


def initialize_exif_for_file(f_id, values: dict):
    """Exif 編集用セッション状態を初期値で書き込む。"""
    for field, val in values.items():
        st.session_state[_exif_ss_key(field, f_id)] = val
    st.session_state["initialized_exif"][f_id] = True


def reset_exif_for_file(f_id):
    """Exif 編集用セッション状態をすべて削除し、初期化フラグも消す。
    次の再レンダリング時に initialize_exif_for_file が再度呼ばれ、
    Exif 抽出値で上書きされる。
    """
    for field in EXIF_FIELDS:
        key = _exif_ss_key(field, f_id)
        st.session_state.pop(key, None)
    st.session_state["initialized_exif"].pop(f_id, None)


def cleanup_stale_session_keys(current_f_ids):
    """アップロードリストから消えたファイルのセッション状態を削除する。"""
    current_set = set(current_f_ids)
    stale = [fid for fid in st.session_state["initialized_exif"] if fid not in current_set]
    for fid in stale:
        reset_exif_for_file(fid)
        st.session_state.pop(f"rot_{fid}", None)
        st.session_state.pop(f"flip_{fid}", None)


# ---------------------------------------------------------------------------
# 画像処理（キャッシュ）
# ---------------------------------------------------------------------------

@st.cache_data(show_spinner=False)
def cached_process_image(
    image_bytes, camera_text, lens_text, fl_text, f_text, ss_text,
    iso_text, time_text, date_text, frame_ratio, banner_ratio,
    show_frame, bg_color, font_scale, font_family_main, font_family_sub,
    rotation_angle, flip_horizontal,
    exif_bytes_override=None, output_format="JPEG", show_banner=True,
    font_bytes_main=None, font_bytes_sub=None,
):
    return process_image(
        image_bytes, camera_text, lens_text, fl_text, f_text, ss_text,
        iso_text, time_text, date_text,
        frame_ratio=frame_ratio, banner_ratio=banner_ratio,
        show_frame=show_frame, bg_color=bg_color,
        font_scale=font_scale, font_family_main=font_family_main,
        font_family_sub=font_family_sub, rotation_angle=rotation_angle,
        flip_horizontal=flip_horizontal, exif_bytes_override=exif_bytes_override,
        output_format=output_format, show_banner=show_banner,
        font_bytes_main=font_bytes_main, font_bytes_sub=font_bytes_sub,
    )


# ---------------------------------------------------------------------------
# フラグメント：1枚分のエディタ UI
# ---------------------------------------------------------------------------

@st.fragment
def render_image_editor(uploaded_file, unique_id, g_params):
    f_id = unique_id
    image_bytes = uploaded_file.getvalue()

    # --- Exif 抽出 ---
    # exif_transpose を extract_exif より先に呼ぶ。
    # Pillowは getexif() 呼び出し後に内部のOrientationタグが失われる場合があり、
    # extract_exif を先に呼ぶと直後の exif_transpose が回転を適用できなくなる。
    img = Image.open(io.BytesIO(image_bytes))
    img_transposed = ImageOps.exif_transpose(img)   # 撮影時の回転を適用済み
    exif_raw = extract_exif(img)
    camera_ext, lens_ext, fl_ext, f_ext, ss_ext, iso_ext, time_ext, date_ext = format_exif_text(exif_raw)
    exif_defaults = dict(
        camera=camera_ext, lens=lens_ext, fl=fl_ext, f=f_ext,
        ss=ss_ext, iso=iso_ext, time=time_ext, date=date_ext,
    )

    # --- セッション初期化（初回のみ） ---
    if not is_exif_initialized(f_id):
        initialize_exif_for_file(f_id, exif_defaults)

    # --- 回転・反転キー初期化 ---
    rot_key  = f"rot_{f_id}"
    flip_key = f"flip_{f_id}"
    st.session_state.setdefault(rot_key,  0)
    st.session_state.setdefault(flip_key, False)

    # --- 出力フォーマット ---
    _out_format, _out_ext, _out_mime = resolve_output_format(f_id, g_params["heic_to_jpeg"])
    _base_name   = f_id.rsplit(".", 1)[0]
    _out_filename = f"framed_{_base_name}.{_out_ext}"

    # --- フォント解決 ---
    font_family_main, font_bytes_main = resolve_font_params(
        g_params["font_choice_main"], g_params["font_options"], g_params["custom_fonts"]
    )
    font_family_sub, font_bytes_sub = resolve_font_params(
        g_params["font_choice_sub"], g_params["font_options"], g_params["custom_fonts"]
    )

    # --- 表示フィルタ適用済みの最終テキスト ---
    def get_field(field):
        return st.session_state.get(_exif_ss_key(field, f_id), "")

    final_camera = get_field("camera") if g_params["show_camera"] else ""
    final_lens   = get_field("lens")   if g_params["show_lens"]   else ""
    final_fl     = get_field("fl")     if g_params["show_fl"]     else ""
    final_f      = get_field("f")      if g_params["show_f"]      else ""
    final_ss     = get_field("ss")     if g_params["show_ss"]     else ""
    final_iso    = get_field("iso")    if g_params["show_iso"]    else ""
    final_date   = get_field("date")   if g_params["show_date"]   else ""
    final_time   = get_field("time")   if g_params["show_time"]   else ""

    # --- 画像処理 ---
    processed_bytes = None
    try:
        processed_bytes = cached_process_image(
            image_bytes=image_bytes,
            camera_text=final_camera, lens_text=final_lens,
            fl_text=final_fl,        f_text=final_f,
            ss_text=final_ss,        iso_text=final_iso,
            time_text=final_time,    date_text=final_date,
            frame_ratio=g_params["frame_ratio"],
            banner_ratio=g_params["banner_ratio"],
            show_frame=g_params["show_frame"],
            bg_color=g_params["bg_color"],
            font_scale=g_params["font_scale"],
            font_family_main=font_family_main,
            font_family_sub=font_family_sub,
            rotation_angle=st.session_state[rot_key],
            flip_horizontal=st.session_state[flip_key],
            exif_bytes_override=get_exif_bytes(img),
            output_format=_out_format,
            show_banner=g_params["show_banner"],
            font_bytes_main=font_bytes_main,
            font_bytes_sub=font_bytes_sub,
        )
        st.session_state["processed_results"][f_id] = (_out_filename, processed_bytes)
    except Exception as e:
        st.error(f"{f_id} の処理中にエラーが発生しました: {e}")

    # --- レイアウト ---
    row_c1, row_c2 = st.columns([1, 4])

    with row_c1:
        if processed_bytes:
            st.image(processed_bytes, use_container_width=True)
        else:
            st.error("プレビュー生成エラー")

    with row_c2:
        st.write(f"📝 **{f_id}**")
        if processed_bytes:
            st.download_button(
                label=f"📥 この画像をダウンロード ({_out_filename})",
                data=processed_bytes,
                file_name=_out_filename,
                mime=_out_mime,
                key=f"dl_btn_{f_id}",
            )

        with st.expander("🛠️ 詳細設定・メタデータ (Details & Metadata)", expanded=False):
            # 回転・反転ボタン：短いボタン3つを左寄せにし、リセットボタンに広いカラムを割り当てる
            btn_col1, btn_col2, btn_col3, btn_col4 = st.columns([1, 1, 1, 2])
            with btn_col1:
                st.button(
                    "↺ 左に回転", key=f"rotate_left_btn_{f_id}",
                    on_click=lambda k=rot_key: st.session_state.update({k: (st.session_state[k] + 90) % 360}),
                )
            with btn_col2:
                st.button(
                    "↻ 右に回転", key=f"rotate_right_btn_{f_id}",
                    on_click=lambda k=rot_key: st.session_state.update({k: (st.session_state[k] - 90) % 360}),
                )
            with btn_col3:
                st.button(
                    "↔️ 左右反転", key=f"flip_btn_{f_id}",
                    on_click=lambda k=flip_key: st.session_state.update({k: not st.session_state[k]}),
                )
            with btn_col4:
                def _do_reset_transform(rk=rot_key, fk=flip_key):
                    st.session_state[rk] = 0
                    st.session_state[fk] = False
                st.button(
                    "🔄 回転・反転をリセット", key=f"reset_transform_btn_{f_id}",
                    on_click=_do_reset_transform,
                    use_container_width=True,
                )

            st.markdown("---")

            tab1, tab2, tab3 = st.tabs(["📝 Exif情報の編集", "🖼️ 元画像のサムネイル", "ℹ️ 元のメタデータ"])

            with tab1:
                st.markdown("##### 📝 Exif情報の編集（フレーム表示分）")
                st.caption("テキストを変更すると、画像フレームの文字がリアルタイムに更新されます。")

                e_col1, e_col2 = st.columns(2)
                with e_col1:
                    st.text_input("カメラモデル (Camera Model)",    key=_exif_ss_key("camera", f_id))
                    st.text_input("レンズモデル (Lens Model)",      key=_exif_ss_key("lens",   f_id))
                    st.text_input("撮影日付 (Date)",                key=_exif_ss_key("date",   f_id))
                    st.text_input("撮影時間 (Time)",                key=_exif_ss_key("time",   f_id))
                with e_col2:
                    st.text_input("焦点距離 (Focal Length)",        key=_exif_ss_key("fl",  f_id))
                    st.text_input("F値 (F-Number)",                 key=_exif_ss_key("f",   f_id))
                    st.text_input("シャッタースピード (SS)",         key=_exif_ss_key("ss",  f_id))
                    st.text_input("ISO",                            key=_exif_ss_key("iso", f_id))

                # リセットボタン：
                # session_state からキーを削除してから rerun することで、
                # 次の再レンダリング時に initialize_exif_for_file が再び呼ばれ
                # Exif 抽出値で確実に初期化される。
                def _do_reset(fid=f_id):
                    reset_exif_for_file(fid)

                st.button(
                    "🔄 この画像の設定を元に戻す",
                    key=f"reset_btn_{f_id}",
                    on_click=_do_reset,
                )

            with tab2:
                st.markdown("##### 🖼️ 元画像のサムネイル")
                # img_transposed は撮影時のExif回転適用済み。
                # さらにユーザーが設定した回転・反転を重ねて表示する。
                preview = img_transposed.copy()
                if st.session_state[flip_key]:
                    preview = ImageOps.mirror(preview)
                if st.session_state[rot_key]:
                    preview = preview.rotate(st.session_state[rot_key], expand=True)
                st.image(preview, use_container_width=True)

            with tab3:
                st.markdown("##### ℹ️ 画像の全メタデータ")
                if exif_raw:
                    json_str = json.dumps(
                        {str(k): str(v) for k, v in exif_raw.items()},
                        indent=2, ensure_ascii=False,
                    )
                    st.markdown(
                        f'<div style="height:300px;overflow-y:auto;border:1px solid rgba(128,128,128,0.3);'
                        f'padding:10px;border-radius:5px;font-family:monospace;white-space:pre-wrap;'
                        f'font-size:0.8rem;">{html.escape(json_str)}</div>',
                        unsafe_allow_html=True,
                    )
                else:
                    st.write("メタデータは存在しません。")


# ---------------------------------------------------------------------------
# サイドバー UI
# ---------------------------------------------------------------------------

def build_sidebar():
    st.markdown("""
    <style>
    div[data-testid="stButton"] button {
        font-size: 0.78rem;
        padding-left: 0.4rem; padding-right: 0.4rem;
        white-space: normal; word-break: keep-all;
    }
    </style>
    """, unsafe_allow_html=True)

    sb = st.sidebar

    sb.header("🎨 デザイン設定 (Design)")
    show_frame   = sb.checkbox("外枠を表示する (Show Frame)",        value=True)
    show_banner  = sb.checkbox("下部バナーを表示する (Show Banner)", value=True)
    bg_color     = sb.radio(
        "カラーテーマ (Color Theme)", ["white", "black"],
        format_func=lambda x: "白 (White)" if x == "white" else "黒 (Black)",
    )
    frame_ratio  = sb.slider("外枠の太さ (Frame Width)",      min_value=1,  max_value=10,  value=2,   step=1)  / 100.0
    banner_ratio = sb.slider("下部バナーの太さ (Banner Height)", min_value=5, max_value=20, value=11, step=1) / 100.0

    sb.markdown("---")
    sb.header("🎨 フォント設定 (Font)")

    uploaded_font_files = sb.file_uploader(
        "カスタムフォントをアップロード (.ttf, .otf)",
        type=["ttf", "otf"], accept_multiple_files=True,
    )
    if uploaded_font_files:
        for f in uploaded_font_files:
            st.session_state["custom_fonts"][f.name] = f.getvalue()
        last_name = uploaded_font_files[-1].name
        if st.session_state.get("last_uploaded_font_name") != last_name:
            st.session_state["last_uploaded_font_name"]  = last_name
            st.session_state["selected_font_main"] = f"カスタム: {last_name}"
            st.session_state["selected_font_sub"]  = f"カスタム: {last_name}"

    font_options = dict(DEFAULT_FONTS)
    for font_name in st.session_state["custom_fonts"]:
        font_options[f"カスタム: {font_name}"] = "uploaded_custom_font"

    option_labels = list(font_options.keys())

    def _safe_index(label, fallback=0):
        try:
            return option_labels.index(label)
        except ValueError:
            return fallback

    st.session_state.setdefault("selected_font_main", "Noto Sans JP (Bold)")
    if st.session_state["selected_font_main"] not in font_options:
        st.session_state["selected_font_main"] = "Noto Sans JP (Bold)"

    st.session_state.setdefault("selected_font_sub", "Noto Sans JP (Regular)")
    if st.session_state["selected_font_sub"] not in font_options:
        st.session_state["selected_font_sub"] = "Noto Sans JP (Regular)"

    font_choice_main = sb.selectbox(
        "メインフォント (Main Font)", option_labels,
        index=_safe_index(st.session_state["selected_font_main"]),
        key="font_choice_main_selectbox",
    )
    st.session_state["selected_font_main"] = font_choice_main

    font_choice_sub = sb.selectbox(
        "サブフォント (Sub Font)", option_labels,
        index=_safe_index(st.session_state["selected_font_sub"]),
        key="font_choice_sub_selectbox",
    )
    st.session_state["selected_font_sub"] = font_choice_sub

    font_scale = sb.slider("文字のサイズ (Font Size)", min_value=50, max_value=200, value=100, step=5) / 100.0

    with sb.expander("Credits & License", expanded=False):
        st.markdown("""
            This application uses fonts below from Google Fonts.
            - Noto Sans Japanese  
                Copyright 2014-2021 Adobe
            - Roboto  
                Copyright 2011 The Roboto Project Authors  

            Licensed under the SIL Open Font License, Version 1.1.
            https://openfontlicense.org

        """)

    sb.markdown("---")
    sb.header("👁️ 表示項目 (Display Items)")
    sb.caption("チェックを外すと、全ての画像でその項目が印字されなくなります。")
    show_camera = sb.checkbox("カメラモデル",       value=True)
    show_lens   = sb.checkbox("レンズモデル",       value=True)
    show_fl     = sb.checkbox("焦点距離",           value=True)
    show_f      = sb.checkbox("F値",               value=True)
    show_ss     = sb.checkbox("シャッタースピード", value=True)
    show_iso    = sb.checkbox("ISO",               value=True)
    show_date   = sb.checkbox("撮影日付",           value=True)
    show_time   = sb.checkbox("撮影時間",           value=True)

    sb.markdown("---")
    sb.header("⚙️ 出力設定 (Output)")
    heic_to_jpeg = sb.checkbox(
        "HEIC / HEIF を JPEG に変換して保存", value=True,
        help="オフにすると HEIC / HEIF 形式のまま保存します。",
    )

    return dict(
        show_frame=show_frame, show_banner=show_banner, bg_color=bg_color,
        frame_ratio=frame_ratio, banner_ratio=banner_ratio,
        font_choice_main=font_choice_main, font_choice_sub=font_choice_sub,
        font_options=font_options, custom_fonts=st.session_state["custom_fonts"],
        font_scale=font_scale,
        show_camera=show_camera, show_lens=show_lens,
        show_fl=show_fl, show_f=show_f, show_ss=show_ss, show_iso=show_iso,
        show_date=show_date, show_time=show_time,
        heic_to_jpeg=heic_to_jpeg,
    )


# ---------------------------------------------------------------------------
# メインエントリポイント
# ---------------------------------------------------------------------------

def main():
    init_session_state()

    st.title("📸 Exif Framer")
    st.markdown("アップロードした写真にフレームとExif情報付きのバナーを追加します。")
    st.info("サイドバーで各種設定ができます。サイドバーが表示されていない場合は、画面左上の「≫」をクリックしてください。")

    g_params = build_sidebar()

    uploaded_files = st.file_uploader(
        "写真を選択してください (複数選択可)",
        type=["jpg", "jpeg", "png", "tif", "tiff", "heic", "heif"],
        accept_multiple_files=True,
    )

    if not uploaded_files:
        return

    deduped_files = deduplicate_filenames(uploaded_files)
    current_f_ids = [uid for uid, _ in deduped_files]

    # 重複通知
    if len(uploaded_files) != len(set(f.name for f in uploaded_files)):
        st.info("同じ名前のファイルが複数アップロードされています。ファイル名に連番を付けて処理します（例: photo_(2).jpg）。")

    # アップロードリストから消えたファイルのセッション状態をクリーンアップ
    cleanup_stale_session_keys(current_f_ids)

    # 各ファイルのエディタを描画
    st.session_state["processed_results"] = {}
    for unique_id, uploaded_file in deduped_files:
        render_image_editor(uploaded_file, unique_id, g_params)

    # ZIP ダウンロード
    filtered_results = [
        st.session_state["processed_results"][fid]
        for fid in current_f_ids
        if fid in st.session_state["processed_results"]
    ]
    if filtered_results:
        st.markdown("---")
        st.subheader("📦 すべての画像をまとめてダウンロード")
        st.write(f"処理が完了した {len(filtered_results)} 枚の画像を一つのZIPファイルとして保存できます。")

        zip_buffer = io.BytesIO()
        with zipfile.ZipFile(zip_buffer, "w", zipfile.ZIP_DEFLATED) as zf:
            for file_name, img_bytes in filtered_results:
                zf.writestr(file_name, img_bytes)

        st.download_button(
            label="📥 すべてZIPでダウンロード (Download All as ZIP)",
            data=zip_buffer.getvalue(),
            file_name="framed_images.zip",
            mime="application/zip",
            type="primary",
            width="stretch",
        )

main()
