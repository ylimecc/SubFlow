"""Tests de los parsers y funciones puras de SubFlow.

No requieren PyQt6 corriendo (ver conftest.py: hace stubs de PyQt6).

Correr:
    pip install pytest
    pytest tests/ -v
"""
import os
import sys

# Asegura que subtitle_editor.py sea importable desde tests/
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest

from subtitle_editor import (
    # modelos
    Cue,
    WordTiming,
    # conversiones
    ms_to_srt_time,
    ms_to_vtt_time,
    ms_to_ass_time,
    time_str_to_ms,
    _ass_time_to_ms,
    # parsers
    parse_srt,
    parse_vtt,
    parse_ass,
    # serializers
    serialize_srt,
    serialize_vtt,
    serialize_ass,
    serialize_txt,
    _serialize_timed,
    # formato
    wrap_two_lines,
    wrap_two_lines_report,
    auto_format_cue_text,
    MAX_LINE_CHARS,
    # encoding helper
    _decode_subtitle_bytes,
    # post-replace cleanup
    _clean_replaced_text,
)


# ---------------------------------------------------------------------------
# Conversion de tiempos
# ---------------------------------------------------------------------------

class TestTimeConversion:
    def test_ms_to_srt_basico(self):
        assert ms_to_srt_time(0) == "00:00:00,000"
        assert ms_to_srt_time(1) == "00:00:00,001"
        assert ms_to_srt_time(1000) == "00:00:01,000"
        assert ms_to_srt_time(61_000) == "00:01:01,000"
        assert ms_to_srt_time(3_723_456) == "01:02:03,456"

    def test_ms_to_srt_negativo_se_normaliza_a_cero(self):
        # ms_to_srt_time clampa negativos a 0 para no escribir SRT raros
        assert ms_to_srt_time(-100) == "00:00:00,000"

    def test_ms_to_vtt_usa_punto(self):
        assert ms_to_vtt_time(3_723_456) == "01:02:03.456"

    def test_ms_to_ass_usa_centisegundos(self):
        # ASS solo tiene 2 digitos de precision (centesimas)
        assert ms_to_ass_time(3_723_456) == "1:02:03.45"
        assert ms_to_ass_time(0) == "0:00:00.00"

    def test_time_str_to_ms_basico(self):
        assert time_str_to_ms("00:00:00,000") == 0
        assert time_str_to_ms("01:02:03,456") == 3_723_456
        assert time_str_to_ms("01:02:03.456") == 3_723_456  # punto o coma
        assert time_str_to_ms("02:03,100") == 123_100  # solo MM:SS

    def test_time_str_to_ms_sin_milisegundos(self):
        assert time_str_to_ms("00:00:05") == 5000

    def test_time_str_to_ms_rechaza_negativos(self):
        # Fix critico: tiempos negativos solian devolverse como positivos
        with pytest.raises(ValueError):
            time_str_to_ms("-00:00:01,000")

    def test_time_str_to_ms_rechaza_formato_invalido(self):
        with pytest.raises(ValueError):
            time_str_to_ms("hola")
        with pytest.raises(ValueError):
            time_str_to_ms("99")  # menos de 2 componentes

    def test_round_trip_srt(self):
        for ms in [0, 1, 999, 1000, 60_000, 3_723_456]:
            assert time_str_to_ms(ms_to_srt_time(ms)) == ms

    def test_round_trip_vtt(self):
        for ms in [0, 1500, 3_723_456]:
            assert time_str_to_ms(ms_to_vtt_time(ms)) == ms

    def test_ass_time_conversion(self):
        assert _ass_time_to_ms("0:00:00.00") == 0
        assert _ass_time_to_ms("1:02:03.45") == 3_723_450


# ---------------------------------------------------------------------------
# Parser SRT
# ---------------------------------------------------------------------------

SAMPLE_SRT = """1
00:00:00,000 --> 00:00:02,500
Hola mundo

2
00:00:02,500 --> 00:00:05,000
Adios
mundo
"""


class TestParseSrt:
    def test_dos_cues_simple(self):
        cues = parse_srt(SAMPLE_SRT)
        assert len(cues) == 2
        assert cues[0].text == "Hola mundo"
        assert cues[0].start_ms == 0
        assert cues[0].end_ms == 2500
        # Multilinea: \n entre lineas preservado
        assert cues[1].text == "Adios\nmundo"

    def test_bom_no_rompe(self):
        # Si alguien llama al parser con texto que conserva el BOM, parser
        # debe ignorarlo (defensa, ademas del utf-8-sig al cargar)
        cues = parse_srt("﻿" + SAMPLE_SRT)
        assert len(cues) == 2
        assert cues[0].text == "Hola mundo"

    def test_crlf_y_lf_indistinguibles(self):
        crlf_version = SAMPLE_SRT.replace("\n", "\r\n")
        cues = parse_srt(crlf_version)
        assert len(cues) == 2
        assert cues[0].text == "Hola mundo"

    def test_lineas_en_blanco_extra(self):
        # Multiples lineas en blanco entre bloques no rompen el parser
        with_extras = SAMPLE_SRT.replace("\n\n", "\n\n\n\n")
        cues = parse_srt(with_extras)
        assert len(cues) == 2

    def test_archivo_sin_numeros(self):
        # Algunos generadores omiten el numero secuencial
        no_index = """00:00:00,000 --> 00:00:02,500
Hola

00:00:02,500 --> 00:00:05,000
Mundo
"""
        cues = parse_srt(no_index)
        assert len(cues) == 2
        assert cues[0].text == "Hola"

    def test_round_trip_srt(self):
        cues = parse_srt(SAMPLE_SRT)
        back = serialize_srt(cues)
        cues2 = parse_srt(back)
        assert len(cues2) == 2
        assert cues2[0].text == cues[0].text
        assert cues2[0].start_ms == cues[0].start_ms
        assert cues2[0].end_ms == cues[0].end_ms
        assert cues2[1].text == cues[1].text


# ---------------------------------------------------------------------------
# Parser VTT
# ---------------------------------------------------------------------------

SAMPLE_VTT = """WEBVTT

00:00:00.000 --> 00:00:02.500
Hola VTT

00:00:02.500 --> 00:00:05.000
Adios
"""


class TestParseVtt:
    def test_basico(self):
        cues = parse_vtt(SAMPLE_VTT)
        assert len(cues) == 2
        assert cues[0].text == "Hola VTT"
        assert cues[0].start_ms == 0
        assert cues[0].end_ms == 2500

    def test_mm_ss_sin_hora(self):
        # VTT permite omitir la hora en cues cortos
        sample = """WEBVTT

00:00.000 --> 00:02.500
Sin hora
"""
        cues = parse_vtt(sample)
        assert len(cues) == 1
        assert cues[0].text == "Sin hora"
        assert cues[0].start_ms == 0
        assert cues[0].end_ms == 2500

    def test_bom_no_rompe(self):
        cues = parse_vtt("﻿" + SAMPLE_VTT)
        assert len(cues) == 2

    def test_round_trip_vtt(self):
        cues = parse_vtt(SAMPLE_VTT)
        back = serialize_vtt(cues)
        # Header WEBVTT presente
        assert back.startswith("WEBVTT\n")
        cues2 = parse_vtt(back)
        assert len(cues2) == 2
        assert cues2[0].text == cues[0].text
        assert cues2[0].start_ms == cues[0].start_ms


# ---------------------------------------------------------------------------
# Parser ASS
# ---------------------------------------------------------------------------

class TestParseAss:
    def test_round_trip_via_serialize_ass(self):
        # Generamos un ASS desde cues y lo volvemos a parsear
        cues_in = [
            Cue(0, 2500, "Hola"),
            Cue(2500, 5000, "Adios"),
        ]
        ass_text = serialize_ass(cues_in)
        cues_out = parse_ass(ass_text)
        assert len(cues_out) == 2
        assert cues_out[0].text == "Hola"
        assert cues_out[0].start_ms == 0
        # ASS pierde precision (solo centesimas) -> tolerar 10ms de diff
        assert abs(cues_out[0].end_ms - 2500) < 20

    def test_quita_tags_ass(self):
        ass_text = """[Script Info]
Title: T

[V4+ Styles]
Format: Name
Style: Default

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
Dialogue: 0,0:00:00.00,0:00:02.50,Default,,0,0,0,,{\\b1}Hola{\\b0} mundo
"""
        cues = parse_ass(ass_text)
        assert len(cues) == 1
        # Los {\\b1} y {\\b0} se quitan
        assert cues[0].text == "Hola mundo"

    def test_bom_no_rompe(self):
        cues_in = [Cue(0, 1000, "Test")]
        ass_text = "﻿" + serialize_ass(cues_in)
        cues_out = parse_ass(ass_text)
        assert len(cues_out) == 1
        assert cues_out[0].text == "Test"


# ---------------------------------------------------------------------------
# Serializers
# ---------------------------------------------------------------------------

class TestSerializers:
    def test_srt_incluye_indices(self):
        cues = [Cue(0, 1000, "Uno"), Cue(1000, 2000, "Dos")]
        out = serialize_srt(cues)
        # Cada bloque empieza con su numero
        assert "\n1\n" in "\n" + out
        assert "\n2\n" in out

    def test_vtt_incluye_header_y_no_indices(self):
        cues = [Cue(0, 1000, "Uno"), Cue(1000, 2000, "Dos")]
        out = serialize_vtt(cues)
        assert out.startswith("WEBVTT\n")
        # No deberia tener "\n1\n" como en SRT
        assert "\n1\n" not in out

    def test_txt_solo_texto(self):
        cues = [Cue(0, 1000, "Uno"), Cue(1000, 2000, "Dos")]
        out = serialize_txt(cues)
        # Sin tiempos
        assert "00:00:00" not in out
        assert "Uno" in out and "Dos" in out

    def test_helper_serialize_timed_consistente(self):
        # SRT y VTT deben producir mismo numero de bloques
        cues = [Cue(0, 1000, "A"), Cue(1000, 2000, "B"), Cue(2000, 3000, "C")]
        srt = serialize_srt(cues)
        vtt = serialize_vtt(cues)
        # SRT: 4 lineas/cue (numero, tiempo, texto, blank) -> 12 lineas + 1 final blank
        # VTT: header (2 lineas) + 3 lineas/cue (tiempo, texto, blank) -> 11 lineas
        # Solo verificar que los 3 textos esten en ambos
        for txt in ("A", "B", "C"):
            assert txt in srt
            assert txt in vtt


# ---------------------------------------------------------------------------
# wrap_two_lines / auto_format
# ---------------------------------------------------------------------------

class TestWrapTwoLines:
    def test_texto_corto_sin_cambios(self):
        assert wrap_two_lines("Hola mundo") == "Hola mundo"

    def test_texto_vacio(self):
        assert wrap_two_lines("") == ""
        res, fits = wrap_two_lines_report("")
        assert res == ""
        assert fits is True

    def test_palabra_unica_huge_no_se_parte(self):
        huge = "X" * 100
        res, fits = wrap_two_lines_report(huge)
        assert "\n" not in res
        # Y reporta que NO cabe en max_chars
        assert fits is False

    def test_texto_largo_se_parte_en_dos(self):
        text = "Esta es una linea suficientemente larga como para que tenga que partirse en dos lineas"
        res = wrap_two_lines(text)
        assert res.count("\n") == 1

    def test_texto_cabe_en_42_chars(self):
        # Si el texto cabe, no se parte
        short = "Hola mundo, esto cabe"
        assert wrap_two_lines(short) == short

    def test_normaliza_espacios(self):
        # Multiples espacios o saltos se colapsan a uno
        text = "Hola    mundo\n\n  bonito"
        res, _ = wrap_two_lines_report(text)
        assert "  " not in res.replace("\n", " ")

    def test_fits_flag_es_correcto(self):
        # Cuando hay un corte que cabe, fits=True
        res, fits = wrap_two_lines_report("Hola mundo, bonito dia hace hoy aqui mismo en casa")
        # 50 chars total, parte en 2 lineas balanceadas que caben en 42 cada una
        assert fits is True
        assert all(len(line) <= MAX_LINE_CHARS for line in res.split("\n"))


class TestAutoFormat:
    def test_normaliza_y_envuelve(self):
        text = "  Hola   mundo  "
        assert auto_format_cue_text(text) == "Hola mundo"

    def test_texto_vacio(self):
        assert auto_format_cue_text("") == ""

    def test_aplica_wrap_de_2_lineas(self):
        long_text = "Una linea larga " * 5
        out = auto_format_cue_text(long_text.strip())
        assert "\n" in out  # se partio en lineas


# ---------------------------------------------------------------------------
# Modelos
# ---------------------------------------------------------------------------

class TestCueModel:
    def test_duration_ms_positiva(self):
        c = Cue(1000, 3500, "test")
        assert c.duration_ms == 2500

    def test_duration_ms_clampa_a_cero(self):
        # Si end < start (corrupcion), duration no debe ser negativa
        c = Cue(5000, 1000, "test")
        assert c.duration_ms == 0

    def test_word_timing_dataclass(self):
        w = WordTiming("hola ", 0, 500)
        assert w.text == "hola "
        assert w.start_ms == 0
        assert w.end_ms == 500


# ---------------------------------------------------------------------------
# Tiempos extremos y edge cases
# ---------------------------------------------------------------------------

class TestTimeEdgeCases:
    def test_srt_mas_de_10_horas(self):
        # Pelicula muy larga / podcast de 12h -> el formato HH soporta 99h
        ms = (10 * 3600 + 30 * 60 + 15) * 1000 + 750  # 10:30:15.750
        assert ms_to_srt_time(ms) == "10:30:15,750"
        assert time_str_to_ms("10:30:15,750") == ms

    def test_srt_99_horas_limite(self):
        # 99h es el maximo razonable que el formato HH:MM:SS soporta
        ms = 99 * 3600 * 1000
        out = ms_to_srt_time(ms)
        # Verificamos que no se desborde a algo raro como "100:00:00"
        assert out.startswith("99:")
        assert time_str_to_ms(out) == ms

    def test_ass_centesimas_redondea_correctamente(self):
        # ASS solo tiene 2 digitos: 1, 5 y 9 ms deben dar todos 0 centesimas
        for ms in (1, 5, 9):
            out = ms_to_ass_time(ms)
            # Cualquier ms < 10 redondea a 00 centesimas
            assert out.endswith(".00"), f"ms={ms} -> {out}"
        # 15 ms = 1 centesima (truncado)
        assert ms_to_ass_time(15).endswith(".01")
        # 99 ms = 9 centesimas
        assert ms_to_ass_time(99).endswith(".09")

    def test_ass_round_trip_pierde_precision_pero_es_estable(self):
        # Convertir->parsear->convertir debe dar exactamente lo mismo
        for ms in (123, 456, 789, 1234, 56789):
            once = ms_to_ass_time(ms)
            parsed = _ass_time_to_ms(once)
            twice = ms_to_ass_time(parsed)
            assert once == twice, f"ms={ms}: {once} -> {parsed} -> {twice}"


# ---------------------------------------------------------------------------
# Parser SRT: edge cases adicionales
# ---------------------------------------------------------------------------

class TestParseSrtEdgeCases:
    def test_archivo_vacio(self):
        assert parse_srt("") == []
        assert parse_srt("\n\n\n") == []

    def test_solo_whitespace(self):
        # Un parser robusto no debe crashear con basura
        assert parse_srt("   \n   \n   ") == []

    def test_un_solo_cue_sin_blank_final(self):
        # Algunos generadores omiten el \n\n del final
        srt = "1\n00:00:00,000 --> 00:00:01,000\nHola"
        cues = parse_srt(srt)
        assert len(cues) == 1
        assert cues[0].text == "Hola"

    def test_round_trip_con_caracteres_especiales(self):
        # Acentos, emojis, comillas tipograficas: deben sobrevivir
        cues_in = [
            Cue(0, 1000, "Mañana llegará pronto"),
            Cue(1000, 2000, "Café con leche ☕"),
            Cue(2000, 3000, "Dijo: «hola»"),
        ]
        srt = serialize_srt(cues_in)
        cues_out = parse_srt(srt)
        assert len(cues_out) == 3
        for orig, back in zip(cues_in, cues_out):
            assert orig.text == back.text


# ---------------------------------------------------------------------------
# Serializers: edge cases
# ---------------------------------------------------------------------------

class TestSerializersEdgeCases:
    def test_lista_vacia_no_crashea(self):
        # Exportar 0 cues debe dar archivos validos (no crashear)
        # serialize_srt y serialize_txt pueden devolver "" o "\n"; ambos OK
        assert serialize_srt([]).strip() == ""
        assert serialize_txt([]).strip() == ""
        assert serialize_vtt([]).startswith("WEBVTT")
        # ASS debe al menos tener las secciones [Script Info] y [Events]
        ass = serialize_ass([])
        assert "[Script Info]" in ass
        assert "[Events]" in ass

    def test_cue_con_texto_vacio(self):
        # Un cue sin texto no deberia romper la serializacion
        cues = [Cue(0, 1000, ""), Cue(1000, 2000, "Hola")]
        srt = serialize_srt(cues)
        # Debe poder re-parsearse sin error
        back = parse_srt(srt)
        # Algunos parsers descartan cues vacios; aceptamos 1 o 2
        assert len(back) in (1, 2)
        assert any(c.text == "Hola" for c in back)


# ---------------------------------------------------------------------------
# Regression: B1 - parse_srt con linea en blanco INTERNA
# ---------------------------------------------------------------------------

class TestParseSrtBlankLineInside:
    """Antes del fix B1, parse_srt usaba re.split por blank lines, lo que
    rompia cualquier cue que contuviera una linea en blanco interna (caso
    real de YouTube y Aegisub al exportar transcripciones con parrafos).
    """

    def test_cue_con_blank_line_interna(self):
        srt = (
            "1\n"
            "00:00:00,000 --> 00:00:05,000\n"
            "Primera linea\n"
            "\n"
            "Segunda despues de blank\n"
            "\n"
            "2\n"
            "00:00:05,000 --> 00:00:10,000\n"
            "Cue siguiente\n"
        )
        cues = parse_srt(srt)
        assert len(cues) == 2, "Cue con blank line interna no debe partirse en dos"
        assert "Primera linea" in cues[0].text
        assert "Segunda despues de blank" in cues[0].text
        assert cues[1].text == "Cue siguiente"

    def test_dos_blank_lines_internas(self):
        # Caso extremo: parrafo + parrafo + parrafo dentro del mismo cue
        srt = (
            "00:00:00,000 --> 00:00:10,000\n"
            "Parrafo A\n"
            "\n"
            "Parrafo B\n"
            "\n"
            "Parrafo C\n"
            "\n"
            "00:00:10,000 --> 00:00:20,000\n"
            "Otro cue\n"
        )
        cues = parse_srt(srt)
        assert len(cues) == 2
        assert "Parrafo A" in cues[0].text
        assert "Parrafo B" in cues[0].text
        assert "Parrafo C" in cues[0].text
        assert cues[1].text == "Otro cue"


# ---------------------------------------------------------------------------
# Regression: B2 - parse_ass con campo Text en posicion no-final
# ---------------------------------------------------------------------------

class TestParseAssFieldOrder:
    """Antes del fix B2, parse_ass asumia que Text siempre era el ultimo campo
    de Format. Si alguien declaraba 'Format: Layer, Text, Start, End, Style',
    las comas en el texto se eatean como separadores.
    """

    def test_format_estandar_text_ultimo(self):
        # Caso clasico: sigue funcionando como antes
        ass = (
            "[Events]\n"
            "Format: Layer, Start, End, Style, Text\n"
            "Dialogue: 0,0:00:00.00,0:00:02.50,Default,Hola, mundo\n"
        )
        cues = parse_ass(ass)
        assert len(cues) == 1
        assert cues[0].text == "Hola, mundo"

    def test_format_no_estandar_text_no_ultimo(self):
        # Bug B2: con Text en posicion 3 de 5, antes asignaba mal
        ass = (
            "[Events]\n"
            "Format: Layer, Start, End, Text, Style\n"
            "Dialogue: 0,0:00:00.00,0:00:02.50,Hola mundo,Default\n"
        )
        cues = parse_ass(ass)
        assert len(cues) == 1
        assert cues[0].text == "Hola mundo"
        assert cues[0].start_ms == 0

    def test_text_con_comas_y_campos_posteriores(self):
        # Escenario incomodo: texto con comas Y campos despues de Text
        ass = (
            "[Events]\n"
            "Format: Start, End, Text, Style\n"
            "Dialogue: 0:00:00.00,0:00:02.50,Hola, mundo, bonito,Default\n"
        )
        cues = parse_ass(ass)
        assert len(cues) == 1
        # Las dos comas iniciales se respetan (Start, End), Style queda al final,
        # el resto es Text.
        assert cues[0].text == "Hola, mundo, bonito"


# ---------------------------------------------------------------------------
# Regression: B4 - _decode_subtitle_bytes con BOM UTF-16
# ---------------------------------------------------------------------------

class TestDecodeSubtitleBytes:
    """Antes del fix B4, abrir un .srt generado por Notepad de Windows
    (UTF-16 LE con BOM) caia en el fallback Latin-1 y mostraba caracteres
    basura tipo 'H\\x00o\\x00l\\x00a\\x00'.
    """

    SAMPLE_TEXT = "1\n00:00:00,000 --> 00:00:02,500\nHola mundo\n"

    def test_utf8_sin_bom(self):
        data = self.SAMPLE_TEXT.encode("utf-8")
        assert _decode_subtitle_bytes(data) == self.SAMPLE_TEXT

    def test_utf8_con_bom(self):
        data = b"\xef\xbb\xbf" + self.SAMPLE_TEXT.encode("utf-8")
        out = _decode_subtitle_bytes(data)
        # El BOM no debe quedar en el resultado
        assert out == self.SAMPLE_TEXT
        assert "﻿" not in out

    def test_utf16_le_con_bom(self):
        # Notepad de Windows guarda asi por default
        data = self.SAMPLE_TEXT.encode("utf-16-le")
        data = b"\xff\xfe" + data
        out = _decode_subtitle_bytes(data)
        assert "Hola mundo" in out
        # No debe tener bytes nulos espureos
        assert "\x00" not in out

    def test_utf16_be_con_bom(self):
        data = b"\xfe\xff" + self.SAMPLE_TEXT.encode("utf-16-be")
        out = _decode_subtitle_bytes(data)
        assert "Hola mundo" in out
        assert "\x00" not in out

    def test_latin1_fallback_para_archivos_viejos(self):
        # Un archivo en Latin-1 sin BOM con acentos (e.g. exportado por un
        # editor viejo). UTF-8 deberia fallar y caer en Latin-1.
        data = "Niño\n".encode("latin-1")
        out = _decode_subtitle_bytes(data)
        assert out == "Niño\n"

    def test_decodificar_acentos_utf8(self):
        text = "Mañana llegará el café\n"
        data = text.encode("utf-8")
        assert _decode_subtitle_bytes(data) == text

    def test_pipeline_completo_utf16_a_parser(self):
        # Validar que un .srt en UTF-16 LE termine con cues correctos
        # despues de pasar por _decode_subtitle_bytes + parse_srt.
        srt_text = (
            "1\n"
            "00:00:00,000 --> 00:00:02,500\n"
            "Hola mundo\n"
            "\n"
            "2\n"
            "00:00:02,500 --> 00:00:05,000\n"
            "Adios\n"
        )
        data = b"\xff\xfe" + srt_text.encode("utf-16-le")
        decoded = _decode_subtitle_bytes(data)
        cues = parse_srt(decoded)
        assert len(cues) == 2
        assert cues[0].text == "Hola mundo"
        assert cues[1].text == "Adios"


# ---------------------------------------------------------------------------
# Helper de limpieza post-replace
# ---------------------------------------------------------------------------

class TestCleanReplacedText:
    def test_quita_linea_blanco_inicial(self):
        # Caso real: "Speaker:\nQue tal" reemplazando "Speaker:" -> "\nQue tal"
        assert _clean_replaced_text("\nQue tal colegas?") == "Que tal colegas?"

    def test_quita_linea_blanco_final(self):
        assert _clean_replaced_text("Hola\n") == "Hola"

    def test_colapsa_multiples_newlines(self):
        assert _clean_replaced_text("Hola\n\n\nMundo") == "Hola\nMundo"

    def test_colapsa_espacios_dobles(self):
        # Reemplazar palabra entera por nada: "el  buen dia" -> "el buen dia"
        assert _clean_replaced_text("Hola  mundo") == "Hola mundo"

    def test_quita_espacios_alrededor_de_newline(self):
        assert _clean_replaced_text("Hola \n  Mundo") == "Hola\nMundo"

    def test_caso_combinado(self):
        # Multiples problemas a la vez
        text = "  Speaker:\n   \nQue   tal\n\ncolegas?\n  "
        # Esperamos: "Speaker:\nQue tal\ncolegas?"
        assert _clean_replaced_text(text) == "Speaker:\nQue tal\ncolegas?"

    def test_texto_normal_no_se_toca(self):
        assert _clean_replaced_text("Hola mundo") == "Hola mundo"
        assert _clean_replaced_text("Linea 1\nLinea 2") == "Linea 1\nLinea 2"
