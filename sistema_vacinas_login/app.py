from flask import Flask, render_template, request, redirect, url_for, flash, session
from werkzeug.security import generate_password_hash, check_password_hash
from functools import wraps
import sqlite3
from pathlib import Path
from datetime import date

app = Flask(__name__)
app.secret_key = "troque-esta-chave-por-uma-segura"
DB = Path(__file__).with_name("sistema_saude.db")

def get_db():
    con = sqlite3.connect(DB)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA foreign_keys = ON")
    return con

def init_db():
    con = get_db()
    con.executescript("""
    CREATE TABLE IF NOT EXISTS usuarios (
      id INTEGER PRIMARY KEY AUTOINCREMENT, pessoa_id INTEGER, usuario TEXT UNIQUE NOT NULL,
      senha_hash TEXT NOT NULL, tipo TEXT NOT NULL DEFAULT 'responsavel', ativo INTEGER DEFAULT 1,
      FOREIGN KEY(pessoa_id) REFERENCES pessoas(id) ON DELETE CASCADE);
    CREATE TABLE IF NOT EXISTS dependentes (
      id INTEGER PRIMARY KEY AUTOINCREMENT, responsavel_id INTEGER NOT NULL, pessoa_id INTEGER NOT NULL,
      parentesco TEXT, UNIQUE(responsavel_id,pessoa_id),
      FOREIGN KEY(responsavel_id) REFERENCES pessoas(id) ON DELETE CASCADE,
      FOREIGN KEY(pessoa_id) REFERENCES pessoas(id) ON DELETE CASCADE);
    CREATE TABLE IF NOT EXISTS pessoas (
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      nome TEXT NOT NULL,
      cpf TEXT UNIQUE,
      nascimento TEXT,
      telefone TEXT,
      endereco TEXT,
      criado_em TEXT DEFAULT CURRENT_TIMESTAMP
    );
    CREATE TABLE IF NOT EXISTS vacinas (
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      nome TEXT NOT NULL UNIQUE,
      fabricante TEXT,
      doses_previstas INTEGER DEFAULT 1
    );
    CREATE TABLE IF NOT EXISTS medicamentos (
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      nome TEXT NOT NULL UNIQUE,
      apresentacao TEXT,
      fabricante TEXT
    );
    CREATE TABLE IF NOT EXISTS aplicacoes_vacinas (
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      pessoa_id INTEGER NOT NULL,
      vacina_id INTEGER NOT NULL,
      dose TEXT NOT NULL,
      data_aplicacao TEXT NOT NULL,
      lote TEXT,
      observacao TEXT,
      FOREIGN KEY(pessoa_id) REFERENCES pessoas(id) ON DELETE CASCADE,
      FOREIGN KEY(vacina_id) REFERENCES vacinas(id) ON DELETE RESTRICT
    );
    CREATE TABLE IF NOT EXISTS medicacoes_pessoa (
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      pessoa_id INTEGER NOT NULL,
      medicamento_id INTEGER NOT NULL,
      data_inicio TEXT,
      data_fim TEXT,
      posologia TEXT,
      observacao TEXT,
      FOREIGN KEY(pessoa_id) REFERENCES pessoas(id) ON DELETE CASCADE,
      FOREIGN KEY(medicamento_id) REFERENCES medicamentos(id) ON DELETE RESTRICT
    );
    """)
    # cria administrador inicial apenas na primeira execução
    if not con.execute("SELECT id FROM usuarios WHERE usuario='admin'").fetchone():
        con.execute("INSERT INTO usuarios(usuario,senha_hash,tipo) VALUES(?,?,?)",
                    ("admin", generate_password_hash("admin123"), "admin"))
    con.commit(); con.close()

def login_required(f):
    @wraps(f)
    def wrapped(*args, **kwargs):
        if not session.get("usuario_id"):
            return redirect(url_for("login"))
        return f(*args, **kwargs)
    return wrapped

def pode_ver(pessoa_id):
    if session.get("tipo") == "admin": return True
    if session.get("pessoa_id") == pessoa_id: return True
    con=get_db()
    ok=con.execute("SELECT id FROM dependentes WHERE responsavel_id=? AND pessoa_id=?",
                   (session.get("pessoa_id"),pessoa_id)).fetchone()
    con.close()
    return bool(ok)

@app.route("/login", methods=["GET","POST"])
def login():
    if request.method=="POST":
        con=get_db()
        u=con.execute("SELECT * FROM usuarios WHERE usuario=? AND ativo=1",(request.form["usuario"],)).fetchone()
        con.close()
        if u and check_password_hash(u["senha_hash"],request.form["senha"]):
            session.clear(); session.update(usuario_id=u["id"], pessoa_id=u["pessoa_id"], tipo=u["tipo"])
            return redirect(url_for("minha_area" if u["tipo"]!="admin" else "index"))
        flash("Usuário ou senha inválidos.","danger")
    return render_template("login.html")

@app.route("/registrar", methods=["GET", "POST"])
def registrar():
    if request.method == "POST":
        nome = request.form["nome"].strip()
        usuario = request.form["usuario"].strip()
        senha = request.form["senha"]
        cpf = request.form.get("cpf") or None
        
        con = get_db()
        try:
            # 1. Cria o registro da pessoa física primeiro
            cursor = con.execute(
                "INSERT INTO pessoas (nome, cpf) VALUES (?, ?)", (nome, cpf)
            )
            pessoa_id = cursor.lastrowid
            
            # 2. Cria o usuário vinculado a essa pessoa como 'responsavel'
            senha_hash = generate_password_hash(senha)
            con.execute(
                "INSERT INTO usuarios (pessoa_id, usuario, senha_hash, tipo) VALUES (?, ?, ?, 'responsavel')",
                (pessoa_id, usuario, senha_hash)
            )
            con.commit()
            flash("Conta criada com sucesso! Faça seu login.", "success")
            return redirect(url_for("login"))
        except sqlite3.IntegrityError:
            flash("Usuário ou CPF já cadastrado.", "danger")
        finally:
            con.close()
            
    return render_template("registrar.html") # Você precisará criar este HTML
    
@app.route("/logout")
def logout():
    session.clear(); return redirect(url_for("login"))

@app.route("/minha-area")
@login_required
def minha_area():
    if session.get("tipo")=="admin": return redirect(url_for("index"))
    con=get_db(); pid=session.get("pessoa_id")
    titular=con.execute("SELECT * FROM pessoas WHERE id=?",(pid,)).fetchone()
    deps=con.execute("SELECT p.*,d.parentesco FROM dependentes d JOIN pessoas p ON p.id=d.pessoa_id WHERE d.responsavel_id=?",(pid,)).fetchall()
    con.close()
    return render_template("minha_area.html", titular=titular, dependentes=deps)

@app.route("/")
def index():
    con=get_db()
    dados = {
      "pessoas": con.execute("SELECT COUNT(*) FROM pessoas").fetchone()[0],
      "aplicacoes": con.execute("SELECT COUNT(*) FROM aplicacoes_vacinas").fetchone()[0],
      "medicacoes": con.execute("SELECT COUNT(*) FROM medicacoes_pessoa WHERE data_fim IS NULL OR data_fim >= ?", (date.today().isoformat(),)).fetchone()[0],
    }
    ultimas = con.execute("""SELECT a.*, p.nome pessoa, v.nome vacina
        FROM aplicacoes_vacinas a JOIN pessoas p ON p.id=a.pessoa_id
        JOIN vacinas v ON v.id=a.vacina_id ORDER BY a.id DESC LIMIT 8""").fetchall()
    con.close()
    return render_template("index.html", dados=dados, ultimas=ultimas)

@app.route("/pessoas", methods=["GET","POST"])
def pessoas():
    con=get_db()
    if request.method=="POST":
        try:
            con.execute("INSERT INTO pessoas(nome,cpf,nascimento,telefone,endereco) VALUES(?,?,?,?,?)",
                (request.form["nome"].strip(), request.form.get("cpf") or None, request.form.get("nascimento"), request.form.get("telefone"), request.form.get("endereco")))
            con.commit(); flash("Pessoa cadastrada com sucesso.", "success")
        except sqlite3.IntegrityError:
            flash("CPF já cadastrado.", "danger")
        con.close(); return redirect(url_for("pessoas"))
    busca=request.args.get("busca","").strip()
    rows=con.execute("SELECT * FROM pessoas WHERE nome LIKE ? OR cpf LIKE ? ORDER BY nome", (f"%{busca}%",f"%{busca}%")).fetchall()
    con.close()
    return render_template("pessoas.html", pessoas=rows, busca=busca)

@app.route("/pessoa/<int:pessoa_id>")
@login_required
def pessoa_detalhe(pessoa_id):
    if not pode_ver(pessoa_id): flash("Sem permissão.", "danger"); return redirect(url_for("minha_area"))
    con=get_db()
    pessoa=con.execute("SELECT * FROM pessoas WHERE id=?", (pessoa_id,)).fetchone()
    if not pessoa: con.close(); return "Pessoa não encontrada",404
    vacinas=con.execute("SELECT * FROM vacinas ORDER BY nome").fetchall()
    medicamentos=con.execute("SELECT * FROM medicamentos ORDER BY nome").fetchall()
    aplicacoes=con.execute("""SELECT a.*, v.nome vacina FROM aplicacoes_vacinas a
      JOIN vacinas v ON v.id=a.vacina_id WHERE a.pessoa_id=? ORDER BY a.data_aplicacao DESC,a.id DESC""",(pessoa_id,)).fetchall()
    medicacoes=con.execute("""SELECT m.*, md.nome medicamento, md.apresentacao FROM medicacoes_pessoa m
      JOIN medicamentos md ON md.id=m.medicamento_id WHERE m.pessoa_id=? ORDER BY m.id DESC""",(pessoa_id,)).fetchall()
    con.close()
    return render_template("pessoa_detalhe.html", pessoa=pessoa, vacinas=vacinas, medicamentos=medicamentos, aplicacoes=aplicacoes, medicacoes=medicacoes, hoje=date.today().isoformat())

@app.post("/pessoa/<int:pessoa_id>/vacina")
def aplicar_vacina(pessoa_id):
    con=get_db()
    con.execute("INSERT INTO aplicacoes_vacinas(pessoa_id,vacina_id,dose,data_aplicacao,lote,observacao) VALUES(?,?,?,?,?,?)",
      (pessoa_id,request.form["vacina_id"],request.form["dose"],request.form["data_aplicacao"],request.form.get("lote"),request.form.get("observacao")))
    con.commit(); con.close(); flash("Vacina registrada.", "success")
    return redirect(url_for("pessoa_detalhe", pessoa_id=pessoa_id))

@app.post("/pessoa/<int:pessoa_id>/medicacao")
def registrar_medicacao(pessoa_id):
    con=get_db()
    con.execute("INSERT INTO medicacoes_pessoa(pessoa_id,medicamento_id,data_inicio,data_fim,posologia,observacao) VALUES(?,?,?,?,?,?)",
      (pessoa_id,request.form["medicamento_id"],request.form.get("data_inicio"),request.form.get("data_fim") or None,request.form.get("posologia"),request.form.get("observacao")))
    con.commit(); con.close(); flash("Medicação registrada.", "success")
    return redirect(url_for("pessoa_detalhe", pessoa_id=pessoa_id))

@app.route("/vacinas", methods=["GET","POST"])
def vacinas():
    con=get_db()
    if request.method=="POST":
        try:
            con.execute("INSERT INTO vacinas(nome,fabricante,doses_previstas) VALUES(?,?,?)",(request.form["nome"],request.form.get("fabricante"),request.form.get("doses_previstas") or 1)); con.commit(); flash("Vacina cadastrada.","success")
        except sqlite3.IntegrityError: flash("Vacina já cadastrada.","danger")
        con.close(); return redirect(url_for("vacinas"))
    rows=con.execute("SELECT * FROM vacinas ORDER BY nome").fetchall(); con.close()
    return render_template("cadastro_base.html", titulo="Vacinas", itens=rows, tipo="vacina")

@app.route("/medicamentos", methods=["GET","POST"])
def medicamentos():
    con=get_db()
    if request.method=="POST":
        try:
            con.execute("INSERT INTO medicamentos(nome,apresentacao,fabricante) VALUES(?,?,?)",(request.form["nome"],request.form.get("apresentacao"),request.form.get("fabricante"))); con.commit(); flash("Medicamento cadastrado.","success")
        except sqlite3.IntegrityError: flash("Medicamento já cadastrado.","danger")
        con.close(); return redirect(url_for("medicamentos"))
    rows=con.execute("SELECT * FROM medicamentos ORDER BY nome").fetchall(); con.close()
    return render_template("cadastro_base.html", titulo="Medicamentos", itens=rows, tipo="medicamento")

@app.post("/excluir/<tipo>/<int:item_id>")
def excluir(tipo,item_id):
    tabela={"pessoa":"pessoas","vacina":"vacinas","medicamento":"medicamentos"}.get(tipo)
    if not tabela: return "Tipo inválido",400
    con=get_db()
    try: con.execute(f"DELETE FROM {tabela} WHERE id=?", (item_id,)); con.commit(); flash("Registro excluído.","warning")
    except sqlite3.IntegrityError: flash("Não é possível excluir: há registros vinculados.","danger")
    con.close()
    return redirect(request.referrer or url_for("index"))

init_db()

if __name__ == "__main__":
    app.run(debug=True)
