#!/usr/bin/env python3
"""Minificador best-effort para o deploy — reduz e ofusca ligeiramente os
assets servidos em produção, mantendo os originais legíveis no repositório.

Uso: minify.py <entrada> <saida> <css|js>

Seguro por design: usa rjsmin/rcssmin (só removem espaços e comentários, não
alteram semântica). Se as libs faltarem ou algo correr mal, copia o original
tal e qual — o deploy NUNCA deve falhar por causa da minificação.
"""
import sys
import shutil


def main() -> int:
    if len(sys.argv) != 4:
        print("uso: minify.py <entrada> <saida> <css|js>", file=sys.stderr)
        return 2
    entrada, saida, tipo = sys.argv[1], sys.argv[2], sys.argv[3]
    try:
        with open(entrada, "r", encoding="utf-8") as f:
            src = f.read()
        if tipo == "css":
            import rcssmin
            out = rcssmin.cssmin(src)
        elif tipo == "js":
            import rjsmin
            out = rjsmin.jsmin(src)
        else:
            raise ValueError(f"tipo desconhecido: {tipo}")
        # Cabeçalho legal na cópia minificada (marca de água no ficheiro servido)
        marca = ("/* (c) CarecaBarber - proprietario. Copia/clonagem proibida. "
                 "Suporte: +238 989 12 22 */\n")
        with open(saida, "w", encoding="utf-8") as f:
            f.write(marca + out)
        red = 100 - (len(out) * 100 // max(len(src), 1))
        print(f"minify {tipo}: {entrada} -{red}%")
        return 0
    except Exception as e:  # noqa: BLE001 - fallback total
        print(f"minify falhou ({e}); a copiar original", file=sys.stderr)
        try:
            shutil.copyfile(entrada, saida)
            return 0
        except Exception as e2:  # noqa: BLE001
            print(f"copia de fallback falhou: {e2}", file=sys.stderr)
            return 1


if __name__ == "__main__":
    sys.exit(main())
