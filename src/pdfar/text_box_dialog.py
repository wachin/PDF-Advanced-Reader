"""F10-6: Text box dialog for adding text with wrapping, font, size, colour."""

from __future__ import annotations

import fitz
from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtWidgets import (
    QColorDialog,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QSpinBox,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from .text_editing import (
    TextBoxSettings, HeaderFooterSettings, WatermarkSettings, BatesNumberSettings,
    ImageInsertSettings, AddImage,
    apply_text_boxes, apply_header_footer, apply_watermark, apply_bates_numbering,
    apply_image_insert,
    TEXT_POSITIONS,
)


class TextBoxDialog(QDialog):
    """Dialog for creating and configuring text boxes."""

    def __init__(self, doc: fitz.Document, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Add Text Box")
        self.setMinimumSize(600, 500)
        self.resize(700, 600)
        
        self._doc = doc
        
        layout = QVBoxLayout(self)
        
        # Tab widget for different text types
        self.tabs = QTabWidget(self)
        layout.addWidget(self.tabs)
        
        # Text Box tab
        self.text_box_tab = self._create_text_box_tab()
        self.tabs.addTab(self.text_box_tab, "Text Box")
        
        # Header/Footer tab
        self.header_footer_tab = self._create_header_footer_tab()
        self.tabs.addTab(self.header_footer_tab, "Header / Footer")
        
        # Watermark tab
        self.watermark_tab = self._create_watermark_tab()
        self.tabs.addTab(self.watermark_tab, "Watermark")
        
        # Bates Numbering tab
        self.bates_tab = self._create_bates_tab()
        self.tabs.addTab(self.bates_tab, "Bates Numbering")
        
        # Image tab
        self.image_tab = self._create_image_tab()
        self.tabs.addTab(self.image_tab, "Image")
        
        # Buttons
        self.btn_box = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel,
            Qt.Orientation.Horizontal,
            self,
        )
        self.btn_box.accepted.connect(self.accept)
        self.btn_box.rejected.connect(self.reject)
        layout.addWidget(self.btn_box)
    
    def _create_text_box_tab(self) -> QWidget:
        """Create the text box configuration tab."""
        tab = QWidget()
        layout = QVBoxLayout(tab)
        
        # Text content
        text_group = QGroupBox("Text Content", tab)
        text_layout = QVBoxLayout(text_group)
        self.text_edit = QPlainTextEdit(text_group)
        self.text_edit.setPlaceholderText("Enter text to add...")
        self.text_edit.setMinimumHeight(100)
        text_layout.addWidget(self.text_edit)
        layout.addWidget(text_group)
        
        # Font settings
        font_group = QGroupBox("Font", tab)
        font_layout = QFormLayout(font_group)
        
        self.font_family = QComboBox(font_group)
        self._populate_fonts()
        font_layout.addRow("Family:", self.font_family)
        
        self.font_style = QComboBox(font_group)
        self.font_family.currentTextChanged.connect(self._update_font_styles)
        font_layout.addRow("Style:", self.font_style)
        
        self.font_size = QDoubleSpinBox(font_group)
        self.font_size.setRange(1.0, 200.0)
        self.font_size.setValue(12.0)
        self.font_size.setSuffix(" pt")
        font_layout.addRow("Size:", self.font_size)
        
        color_layout = QHBoxLayout()
        self.font_color = QLineEdit("#000000", font_group)
        self.font_color.setMaximumWidth(80)
        self.color_btn = QPushButton("Pick...", font_group)
        self.color_btn.clicked.connect(self._pick_color)
        color_layout.addWidget(self.font_color)
        color_layout.addWidget(self.color_btn)
        color_layout.addStretch(1)
        font_layout.addRow("Color:", color_layout)
        
        self.alignment = QComboBox(font_group)
        self.alignment.addItems(["Left", "Center", "Right", "Justify"])
        font_layout.addRow("Alignment:", self.alignment)
        
        layout.addWidget(font_group)
        
        # Box settings
        box_group = QGroupBox("Box Settings", tab)
        box_layout = QFormLayout(box_group)
        
        self.box_width = QDoubleSpinBox(box_group)
        self.box_width.setRange(10.0, 2000.0)
        self.box_width.setValue(300.0)
        self.box_width.setSuffix(" pt")
        box_layout.addRow("Width:", self.box_width)
        
        self.box_height = QDoubleSpinBox(box_group)
        self.box_height.setRange(10.0, 2000.0)
        self.box_height.setValue(200.0)
        self.box_height.setSuffix(" pt")
        box_layout.addRow("Height:", self.box_height)
        
        self.word_wrap = QCheckBox("Word wrap", box_group)
        self.word_wrap.setChecked(True)
        box_layout.addRow(self.word_wrap)
        
        self.line_spacing = QDoubleSpinBox(box_group)
        self.line_spacing.setRange(0.5, 3.0)
        self.line_spacing.setValue(1.2)
        self.line_spacing.setSingleStep(0.1)
        box_layout.addRow("Line spacing:", self.line_spacing)
        
        layout.addWidget(box_group)
        
        # Position
        pos_group = QGroupBox("Position", tab)
        pos_layout = QFormLayout(pos_group)
        
        self.position = QComboBox(pos_group)
        for key, label in TEXT_POSITIONS:
            self.position.addItem(label, key)
        pos_layout.addRow("Position:", self.position)
        
        self.x_offset = QDoubleSpinBox(pos_group)
        self.x_offset.setRange(0.0, 2000.0)
        self.x_offset.setValue(36.0)
        self.x_offset.setSuffix(" pt")
        pos_layout.addRow("X offset:", self.x_offset)
        
        self.y_offset = QDoubleSpinBox(pos_group)
        self.y_offset.setRange(0.0, 2000.0)
        self.y_offset.setValue(36.0)
        self.y_offset.setSuffix(" pt")
        pos_layout.addRow("Y offset:", self.y_offset)
        
        self.rotation = QDoubleSpinBox(pos_group)
        self.rotation.setRange(-360.0, 360.0)
        self.rotation.setValue(0.0)
        self.rotation.setSuffix(" °")
        pos_layout.addRow("Rotation:", self.rotation)
        
        layout.addWidget(pos_group)
        
        # Page range
        range_group = QGroupBox("Page Range", tab)
        range_layout = QFormLayout(range_group)
        
        self.apply_to_all = QCheckBox("Apply to all pages", range_group)
        self.apply_to_all.setChecked(True)
        range_layout.addRow(self.apply_to_all)
        
        self.from_page = QSpinBox(range_group)
        self.from_page.setRange(1, 9999)
        self.from_page.setValue(1)
        self.from_page.setEnabled(False)
        range_layout.addRow("From page:", self.from_page)
        
        self.to_page = QSpinBox(range_group)
        self.to_page.setRange(1, 9999)
        self.to_page.setValue(1)
        self.to_page.setEnabled(False)
        range_layout.addRow("To page:", self.to_page)
        
        self.apply_to_all.toggled.connect(lambda checked: (
            self.from_page.setEnabled(not checked),
            self.to_page.setEnabled(not checked),
        ))
        
        layout.addWidget(range_group)
        
        layout.addStretch(1)
        return tab
    
    def _populate_fonts(self):
        """Populate font family combo with available fonts."""
        from .fonts import all_families, BASE14_FONTS
        
        # Add Base14 fonts first
        base14_names = sorted(set(f.family for f in BASE14_FONTS))
        for name in base14_names:
            self.font_family.addItem(f"{name} (Base14)", name)
        
        # Add system fonts
        system_fonts = sorted(all_families())
        for name in system_fonts:
            if name not in base14_names:
                self.font_family.addItem(name, name)
        
        self.font_family.setCurrentIndex(0)
        self._update_font_styles()
    
    def _update_font_styles(self):
        """Update font style combo based on selected family."""
        from .fonts import BASE14_FONTS, styles_for, BASE14_KEYS
        
        self.font_style.clear()
        family = self.font_family.currentData()
        
        # Check if it's a Base14 font
        base14_styles = set()
        for f in BASE14_FONTS:
            if f.family == family:
                # Extract style from name (e.g., Helvetica-Bold -> Bold)
                if '-' in f.name:
                    style = f.name.split('-')[-1]
                    base14_styles.add(style)
        
        if base14_styles:
            for style in sorted(base14_styles):
                self.font_style.addItem(style)
        else:
            # System font
            system_styles = styles_for(family)
            for style in system_styles:
                self.font_style.addItem(style)
        
        if self.font_style.count() == 0:
            self.font_style.addItem("Regular")
    
    def _pick_color(self):
        """Open color picker for font color."""
        color = QColorDialog.getColor()
        if color.isValid():
            self.font_color.setText(color.name())
    
    def _create_header_footer_tab(self) -> QWidget:
        """Create header/footer configuration tab."""
        tab = QWidget()
        layout = QVBoxLayout(tab)
        
        # Header section
        header_group = QGroupBox("Header", tab)
        header_layout = QFormLayout(header_group)
        
        self.header_text = QLineEdit(header_group)
        self.header_text.setPlaceholderText("Header text (use {number}, {total} for page numbers)")
        header_layout.addRow("Text:", self.header_text)
        
        self.header_font = QComboBox(header_group)
        self._populate_fonts_for_combo(self.header_font)
        header_layout.addRow("Font:", self.header_font)
        
        self.header_font_size = QDoubleSpinBox(header_group)
        self.header_font_size.setRange(1.0, 72.0)
        self.header_font_size.setValue(9.0)
        self.header_font_size.setSuffix(" pt")
        header_layout.addRow("Font size:", self.header_font_size)
        
        self.header_color = QLineEdit("#666666", header_group)
        self.header_color.setMaximumWidth(80)
        header_color_btn = QPushButton("Pick...", header_group)
        header_color_btn.clicked.connect(lambda: self._pick_color_for(self.header_color))
        header_color_layout = QHBoxLayout()
        header_color_layout.addWidget(self.header_color)
        header_color_layout.addWidget(header_color_btn)
        header_layout.addRow("Color:", header_color_layout)
        
        self.header_align = QComboBox(header_group)
        self.header_align.addItems(["Left", "Center", "Right"])
        header_layout.addRow("Alignment:", self.header_align)
        
        layout.addWidget(header_group)
        
        # Footer section
        footer_group = QGroupBox("Footer", tab)
        footer_layout = QFormLayout(footer_group)
        
        self.footer_text = QLineEdit(footer_group)
        self.footer_text.setPlaceholderText("Footer text (use {number}, {total} for page numbers)")
        footer_layout.addRow("Text:", self.footer_text)
        
        self.footer_font = QComboBox(footer_group)
        self._populate_fonts_for_combo(self.footer_font)
        footer_layout.addRow("Font:", self.footer_font)
        
        self.footer_font_size = QDoubleSpinBox(footer_group)
        self.footer_font_size.setRange(1.0, 72.0)
        self.footer_font_size.setValue(9.0)
        self.footer_font_size.setSuffix(" pt")
        footer_layout.addRow("Font size:", self.footer_font_size)
        
        self.footer_color = QLineEdit("#666666", footer_group)
        self.footer_color.setMaximumWidth(80)
        footer_color_btn = QPushButton("Pick...", footer_group)
        footer_color_btn.clicked.connect(lambda: self._pick_color_for(self.footer_color))
        footer_color_layout = QHBoxLayout()
        footer_color_layout.addWidget(self.footer_color)
        footer_color_layout.addWidget(footer_color_btn)
        footer_layout.addRow("Color:", footer_color_layout)
        
        self.footer_align = QComboBox(footer_group)
        self.footer_align.addItems(["Left", "Center", "Right"])
        footer_layout.addRow("Alignment:", self.footer_align)
        
        layout.addWidget(footer_group)
        
        # Options
        opt_group = QGroupBox("Options", tab)
        opt_layout = QFormLayout(opt_group)
        
        self.hf_include_page_num = QCheckBox("Include page number", opt_group)
        self.hf_include_page_num.setChecked(True)
        opt_layout.addRow(self.hf_include_page_num)
        
        self.hf_start_number = QSpinBox(opt_group)
        self.hf_start_number.setRange(1, 999999)
        self.hf_start_number.setValue(1)
        opt_layout.addRow("Start number:", self.hf_start_number)
        
        self.hf_different_first = QCheckBox("Different first page", opt_group)
        opt_layout.addRow(self.hf_different_first)
        
        self.hf_first_header = QLineEdit(opt_group)
        self.hf_first_header.setPlaceholderText("First page header (if different)")
        opt_layout.addRow("First page header:", self.hf_first_header)
        
        self.hf_first_footer = QLineEdit(opt_group)
        self.hf_first_footer.setPlaceholderText("First page footer (if different)")
        opt_layout.addRow("First page footer:", self.hf_first_footer)
        
        self.hf_margin = QDoubleSpinBox(opt_group)
        self.hf_margin.setRange(0.0, 500.0)
        self.hf_margin.setValue(36.0)
        self.hf_margin.setSuffix(" pt")
        opt_layout.addRow("Margin:", self.hf_margin)
        
        layout.addWidget(opt_group)
        
        # Page range
        range_group = self._create_page_range_group(tab)
        layout.addWidget(range_group)
        
        layout.addStretch(1)
        return tab
    
    def _populate_fonts_for_combo(self, combo: QComboBox):
        from .fonts import BASE14_FONTS, all_families
        base14_names = sorted(set(f.family for f in BASE14_FONTS))
        for name in base14_names:
            combo.addItem(f"{name} (Base14)", name)
        system_fonts = sorted(all_families())
        for name in system_fonts:
            if name not in base14_names:
                combo.addItem(name, name)
    
    def _pick_color_for(self, line_edit: QLineEdit):
        color = QColorDialog.getColor()
        if color.isValid():
            line_edit.setText(color.name())
    
    def _create_watermark_tab(self) -> QWidget:
        """Create watermark configuration tab."""
        tab = QWidget()
        layout = QVBoxLayout(tab)
        
        text_group = QGroupBox("Watermark Text", tab)
        text_layout = QVBoxLayout(text_group)
        self.wm_text = QPlainTextEdit(text_group)
        self.wm_text.setPlaceholderText("Watermark text (e.g., CONFIDENTIAL, DRAFT)")
        self.wm_text.setMinimumHeight(80)
        text_layout.addWidget(self.wm_text)
        layout.addWidget(text_group)
        
        font_group = QGroupBox("Font", tab)
        font_layout = QFormLayout(font_group)
        
        self.wm_font = QComboBox(font_group)
        self._populate_fonts_for_combo(self.wm_font)
        font_layout.addRow("Family:", self.wm_font)
        
        self.wm_font_size = QDoubleSpinBox(font_group)
        self.wm_font_size.setRange(10.0, 200.0)
        self.wm_font_size.setValue(72.0)
        self.wm_font_size.setSuffix(" pt")
        font_layout.addRow("Size:", self.wm_font_size)
        
        self.wm_color = QLineEdit("#CCCCCC", font_group)
        self.wm_color.setMaximumWidth(80)
        wm_color_btn = QPushButton("Pick...", font_group)
        wm_color_btn.clicked.connect(lambda: self._pick_color_for(self.wm_color))
        wm_color_layout = QHBoxLayout()
        wm_color_layout.addWidget(self.wm_color)
        wm_color_layout.addWidget(wm_color_btn)
        font_layout.addRow("Color:", wm_color_layout)
        
        self.wm_opacity = QDoubleSpinBox(font_group)
        self.wm_opacity.setRange(0.0, 1.0)
        self.wm_opacity.setValue(0.3)
        self.wm_opacity.setSingleStep(0.05)
        font_layout.addRow("Opacity:", self.wm_opacity)
        
        self.wm_rotation = QDoubleSpinBox(font_group)
        self.wm_rotation.setRange(-360.0, 360.0)
        self.wm_rotation.setValue(45.0)
        self.wm_rotation.setSuffix(" °")
        font_layout.addRow("Rotation:", self.wm_rotation)
        
        layout.addWidget(font_group)
        
        # Options
        opt_group = QGroupBox("Options", tab)
        opt_layout = QFormLayout(opt_group)
        
        self.wm_repeat = QCheckBox("Tile across page", tab)
        opt_layout.addRow(self.wm_repeat)
        
        self.wm_h_spacing = QDoubleSpinBox(tab)
        self.wm_h_spacing.setRange(50.0, 1000.0)
        self.wm_h_spacing.setValue(200.0)
        self.wm_h_spacing.setSuffix(" pt")
        opt_layout.addRow("Horizontal spacing:", self.wm_h_spacing)
        
        self.wm_v_spacing = QDoubleSpinBox(tab)
        self.wm_v_spacing.setRange(50.0, 1000.0)
        self.wm_v_spacing.setValue(150.0)
        self.wm_v_spacing.setSuffix(" pt")
        opt_layout.addRow("Vertical spacing:", self.wm_v_spacing)
        
        layout.addWidget(opt_group)
        
        # Page range
        range_group = self._create_page_range_group(tab)
        layout.addWidget(range_group)
        
        layout.addStretch(1)
        return tab
    
    def _create_bates_tab(self) -> QWidget:
        """Create Bates numbering configuration tab."""
        tab = QWidget()
        layout = QVBoxLayout(tab)
        
        num_group = QGroupBox("Numbering Format", tab)
        num_layout = QFormLayout(num_group)
        
        self.bates_prefix = QLineEdit(tab)
        num_layout.addRow("Prefix:", self.bates_prefix)
        
        self.bates_suffix = QLineEdit(tab)
        num_layout.addRow("Suffix:", self.bates_suffix)
        
        self.bates_format = QLineEdit("{number:06d}", tab)
        num_layout.addRow("Format:", self.bates_format)
        layout.addWidget(QLabel("Use {number} for the page number (e.g., {number:06d} = 000001)"))
        
        self.bates_start = QSpinBox(tab)
        self.bates_start.setRange(1, 999999)
        self.bates_start.setValue(1)
        num_layout.addRow("Start number:", self.bates_start)
        
        layout.addWidget(num_group)
        
        font_group = QGroupBox("Font", tab)
        font_layout = QFormLayout(font_group)
        
        self.bates_font = QComboBox(tab)
        self._populate_fonts_for_combo(self.bates_font)
        font_layout.addRow("Family:", self.bates_font)
        
        self.bates_font_size = QDoubleSpinBox(tab)
        self.bates_font_size.setRange(6.0, 24.0)
        self.bates_font_size.setValue(9.0)
        self.bates_font_size.setSuffix(" pt")
        font_layout.addRow("Size:", self.bates_font_size)
        
        self.bates_color = QLineEdit("#000000", tab)
        self.bates_color.setMaximumWidth(80)
        bates_color_btn = QPushButton("Pick...", tab)
        bates_color_btn.clicked.connect(lambda: self._pick_color_for(self.bates_color))
        bates_color_layout = QHBoxLayout()
        bates_color_layout.addWidget(self.bates_color)
        bates_color_layout.addWidget(bates_color_btn)
        font_layout.addRow("Color:", bates_color_layout)
        
        layout.addWidget(font_group)
        
        pos_group = QGroupBox("Position", tab)
        pos_layout = QFormLayout(pos_group)
        
        self.bates_position = QComboBox(tab)
        for key, label in TEXT_POSITIONS:
            self.bates_position.addItem(label, key)
        self.bates_position.setCurrentIndex(8)  # bottom-right
        pos_layout.addRow("Position:", self.bates_position)
        
        self.bates_margin = QDoubleSpinBox(tab)
        self.bates_margin.setRange(0.0, 200.0)
        self.bates_margin.setValue(36.0)
        self.bates_margin.setSuffix(" pt")
        pos_layout.addRow("Margin:", self.bates_margin)
        
        layout.addWidget(pos_group)
        
        skip_group = QGroupBox("Skip Pages", tab)
        skip_layout = QFormLayout(skip_group)
        
        self.bates_skip = QSpinBox(tab)
        self.bates_skip.setRange(0, 9999)
        self.bates_skip.setValue(0)
        skip_layout.addRow("Skip first N pages:", self.bates_skip)
        
        layout.addWidget(skip_group)
        
        # Page range
        range_group = self._create_page_range_group(tab)
        layout.addWidget(range_group)
        
        layout.addStretch(1)
        return tab

    def _create_image_tab(self) -> QWidget:
        """Create image insertion configuration tab."""
        tab = QWidget()
        layout = QVBoxLayout(tab)
        
        # Image source
        source_group = QGroupBox("Image Source", tab)
        source_layout = QVBoxLayout(source_group)
        
        self.img_file_label = QLineEdit(tab)
        self.img_file_label.setPlaceholderText("No image selected")
        self.img_file_label.setReadOnly(True)
        source_layout.addWidget(self.img_file_label)
        
        img_btn_layout = QHBoxLayout()
        self.img_browse_btn = QPushButton("Browse...", tab)
        self.img_browse_btn.clicked.connect(self._browse_image)
        img_btn_layout.addWidget(self.img_browse_btn)
        
        self.img_clear_btn = QPushButton("Clear", tab)
        self.img_clear_btn.clicked.connect(self._clear_image)
        self.img_clear_btn.setEnabled(False)
        img_btn_layout.addWidget(self.img_clear_btn)
        img_btn_layout.addStretch(1)
        source_layout.addLayout(img_btn_layout)
        
        layout.addWidget(source_group)
        
        # Image preview
        preview_group = QGroupBox("Preview", tab)
        preview_layout = QVBoxLayout(preview_group)
        self.img_preview = QLabel(tab)
        self.img_preview.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.img_preview.setMinimumHeight(150)
        self.img_preview.setStyleSheet("border: 1px solid gray; background: #f0f0f0;")
        self.img_preview.setText("No image selected")
        preview_layout.addWidget(self.img_preview)
        layout.addWidget(preview_group)
        
        # Placement
        place_group = QGroupBox("Placement", tab)
        place_layout = QFormLayout(place_group)
        
        self.img_position = QComboBox(tab)
        for key, label in TEXT_POSITIONS:
            self.img_position.addItem(label, key)
        self.img_position.setCurrentIndex(4)  # center
        place_layout.addRow("Position:", self.img_position)
        
        self.img_x = QDoubleSpinBox(tab)
        self.img_x.setRange(0.0, 2000.0)
        self.img_x.setValue(100.0)
        self.img_x.setSuffix(" pt")
        place_layout.addRow("X:", self.img_x)
        
        self.img_y = QDoubleSpinBox(tab)
        self.img_y.setRange(0.0, 2000.0)
        self.img_y.setValue(100.0)
        self.img_y.setSuffix(" pt")
        place_layout.addRow("Y:", self.img_y)
        
        self.img_width = QDoubleSpinBox(tab)
        self.img_width.setRange(10.0, 2000.0)
        self.img_width.setValue(200.0)
        self.img_width.setSuffix(" pt")
        place_layout.addRow("Width:", self.img_width)
        
        self.img_height = QDoubleSpinBox(tab)
        self.img_height.setRange(10.0, 2000.0)
        self.img_height.setValue(100.0)
        self.img_height.setSuffix(" pt")
        place_layout.addRow("Height:", self.img_height)
        
        self.img_keep_proportion = QCheckBox("Keep proportions", tab)
        self.img_keep_proportion.setChecked(True)
        place_layout.addRow(self.img_keep_proportion)
        
        self.img_rotation = QDoubleSpinBox(tab)
        self.img_rotation.setRange(-360.0, 360.0)
        self.img_rotation.setValue(0.0)
        self.img_rotation.setSuffix(" °")
        place_layout.addRow("Rotation:", self.img_rotation)
        
        self.img_opacity = QDoubleSpinBox(tab)
        self.img_opacity.setRange(0.0, 1.0)
        self.img_opacity.setValue(1.0)
        self.img_opacity.setSingleStep(0.05)
        place_layout.addRow("Opacity:", self.img_opacity)
        
        layout.addWidget(place_group)
        
        # Options
        opt_group = QGroupBox("Options", tab)
        opt_layout = QFormLayout(opt_group)
        
        self.img_overlay = QCheckBox("Overlay (on top of content)", tab)
        self.img_overlay.setChecked(True)
        opt_layout.addRow(self.img_overlay)
        
        layout.addWidget(opt_group)
        
        # Page range
        range_group = self._create_page_range_group(tab)
        layout.addWidget(range_group)
        
        layout.addStretch(1)
        return tab

    def _browse_image(self):
        """Open file dialog to select an image."""
        from PyQt6.QtWidgets import QFileDialog
        path, _ = QFileDialog.getOpenFileName(
            self, "Select Image", "",
            "Images (*.jpg *.jpeg *.png *.bmp *.tiff *.gif);;All Files (*)"
        )
        if path:
            self._load_image(path)
    
    def _load_image(self, path: str):
        """Load image from path and update preview."""
        try:
            pixmap = QPixmap(path)
            if pixmap.isNull():
                QMessageBox.warning(self, "Image Error", "Could not load image")
                return
            
            # Scale preview to fit
            preview_size = self.img_preview.size()
            if preview_size.width() > 0 and preview_size.height() > 0:
                scaled = pixmap.scaled(
                    preview_size.width() - 10,
                    preview_size.height() - 10,
                    Qt.AspectRatioMode.KeepAspectRatio,
                    Qt.TransformationMode.SmoothTransformation
                )
                self.img_preview.setPixmap(scaled)
            else:
                self.img_preview.setPixmap(pixmap)
            
            self.img_file_label.setText(path)
            self.img_clear_btn.setEnabled(True)
            self._image_path = path
            self._image_bytes = None
            
        except Exception as e:
            QMessageBox.warning(self, "Image Error", f"Could not load image: {e}")
    
    def _clear_image(self):
        """Clear the selected image."""
        self.img_file_label.clear()
        self.img_preview.clear()
        self.img_preview.setText("No image selected")
        self.img_clear_btn.setEnabled(False)
        self._image_path = None
        self._image_bytes = None
    
    def get_image_settings(self) -> ImageInsertSettings:
        """Get ImageInsertSettings from UI."""
        # Read image bytes if file selected (for JPEG without recompression)
        image_bytes = None
        if hasattr(self, '_image_path') and self._image_path:
            try:
                with open(self._image_path, 'rb') as f:
                    image_bytes = f.read()
            except Exception:
                pass
        
        return ImageInsertSettings(
            image_path=self._image_path if hasattr(self, '_image_path') else "",
            image_bytes=image_bytes,
            rect=(
                self.img_x.value(),
                self.img_y.value(),
                self.img_x.value() + self.img_width.value(),
                self.img_y.value() + self.img_height.value(),
            ),
            rotation=self.img_rotation.value(),
            keep_proportion=self.img_keep_proportion.isChecked(),
            overlay=self.img_overlay.isChecked(),
            opacity=self.img_opacity.value(),
        )

    def _create_page_range_group(self, parent: QWidget) -> QGroupBox:
        """Create a page range selection group."""
        group = QGroupBox("Page Range", parent)
        layout = QFormLayout(group)
        
        apply_all = QCheckBox("Apply to all pages", group)
        apply_all.setChecked(True)
        layout.addRow(apply_all)
        
        from_page = QSpinBox(group)
        from_page.setRange(1, 9999)
        from_page.setValue(1)
        from_page.setEnabled(False)
        layout.addRow("From page:", from_page)
        
        to_page = QSpinBox(group)
        to_page.setRange(1, 9999)
        to_page.setValue(1)
        to_page.setEnabled(False)
        layout.addRow("To page:", to_page)
        
        apply_all.toggled.connect(lambda checked: (
            from_page.setEnabled(not checked),
            to_page.setEnabled(not checked),
        ))
        
        # Store references
        group.apply_all = apply_all
        group.from_page = from_page
        group.to_page = to_page
        
        return group
    
    def get_page_range(self, group: QGroupBox, total_pages: int) -> Optional[Tuple[int, int]]:
        """Get page range from a page range group."""
        if group.apply_all.isChecked():
            return None
        first = max(1, min(group.from_page.value(), total_pages)) - 1
        last = max(1, min(group.to_page.value(), total_pages)) - 1
        if first > last:
            first, last = last, first
        return (first, last)
    
    def get_text_box_settings(self) -> TextBoxSettings:
        """Get TextBoxSettings from UI."""
        return TextBoxSettings(
            text=self.text_edit.toPlainText(),
            font_family=self.font_family.currentData(),
            font_style=self.font_style.currentText(),
            font_size=self.font_size.value(),
            font_color=self.font_color.text(),
            alignment=self.alignment.currentText().lower(),
            box_width=self.box_width.value(),
            box_height=self.box_height.value(),
            position=self.position.currentData(),
            x_offset=self.x_offset.value(),
            y_offset=self.y_offset.value(),
            rotation=self.rotation.value(),
            word_wrap=self.word_wrap.isChecked(),
            line_spacing=self.line_spacing.value(),
        )
    
    def get_header_footer_settings(self) -> HeaderFooterSettings:
        """Get HeaderFooterSettings from UI."""
        return HeaderFooterSettings(
            header_text=self.header_text.text(),
            footer_text=self.footer_text.text(),
            font_family=self.header_font.currentData(),
            font_style="Regular",  # Not exposed in UI yet
            font_size=self.header_font_size.value(),
            font_color=self.header_color.text(),
            alignment=self.header_align.currentText().lower(),
            margin=self.hf_margin.value(),
            include_page_number=self.hf_include_page_num.isChecked(),
            page_number_format="{number}",
            start_number=self.hf_start_number.value(),
            first_page_only=False,
            different_first_page=self.hf_different_first.isChecked(),
            first_page_header=self.hf_first_header.text(),
            first_page_footer=self.hf_first_footer.text(),
        )
    
    def get_watermark_settings(self) -> WatermarkSettings:
        """Get WatermarkSettings from UI."""
        return WatermarkSettings(
            text=self.wm_text.toPlainText(),
            font_family=self.wm_font.currentData(),
            font_style="Bold",
            font_size=self.wm_font_size.value(),
            font_color=self.wm_color.text(),
            opacity=self.wm_opacity.value(),
            rotation=self.wm_rotation.value(),
            position="center",
            repeat=self.wm_repeat.isChecked(),
            horizontal_spacing=self.wm_h_spacing.value(),
            vertical_spacing=self.wm_v_spacing.value(),
        )
    
    def get_bates_settings(self) -> BatesNumberSettings:
        """Get BatesNumberSettings from UI."""
        return BatesNumberSettings(
            prefix=self.bates_prefix.text(),
            suffix=self.bates_suffix.text(),
            start_number=self.bates_start.value(),
            number_format=self.bates_format.text(),
            font_family=self.bates_font.currentData(),
            font_style="Regular",
            font_size=self.bates_font_size.value(),
            font_color=self.bates_color.text(),
            position=self.bates_position.currentData(),
            margin=self.bates_margin.value(),
            pages_to_skip=self.bates_skip.value(),
        )