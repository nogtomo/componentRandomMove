# Maya Python UI: Random Triangulate (Triangulate Default OFF)
import maya.cmds as cmds
import random
import re

WIN_NAME = "mokaRandomTriangulateUI"

def _get_mesh_transforms_from_selection():
    sel = cmds.ls(sl=True, long=True) or []
    out, seen = [], set()

    for s in sel:
        # component -> transform
        if "." in s:
            s = s.split(".", 1)[0]

        # shape -> parent transform
        if cmds.objExists(s) and cmds.nodeType(s) == "mesh":
            p = cmds.listRelatives(s, parent=True, fullPath=True) or []
            if p:
                s = p[0]

        if not cmds.objExists(s) or cmds.nodeType(s) != "transform":
            continue

        shapes = cmds.listRelatives(s, shapes=True, fullPath=True, type="mesh") or []
        # intermediate shapes除外
        shapes = [sh for sh in shapes if not cmds.getAttr(sh + ".intermediateObject")]

        if shapes and s not in seen:
            out.append(s); seen.add(s)

    return out

def _boundary_vertex_indices(obj):
    """Boundary edges (connected to exactly 1 face) -> collect their vertex indices."""
    boundary_verts = set()
    edge_count = cmds.polyEvaluate(obj, edge=True) or 0

    for e in range(edge_count):
        edge = f"{obj}.e[{e}]"
        info = cmds.polyInfo(edge, edgeToFace=True)
        if not info:
            continue

        line = info[0].strip()
        if ":" not in line:
            continue

        right = line.split(":", 1)[1].strip()
        faces = []
        for t in right.split():
            try:
                faces.append(int(t))
            except Exception:
                pass

        # boundary edge if only one face is connected
        if len(faces) != 1:
            continue

        vinfo = cmds.polyInfo(edge, edgeToVertex=True)
        if not vinfo:
            continue

        nums = re.findall(r"\d+", vinfo[0])
        # nums example: ["5","12","13"] -> first is edge index, rest are vertex indices
        for n in nums[1:]:
            boundary_verts.add(int(n))

    return boundary_verts

def _edge_to_faces(obj, e_index):
    info = cmds.polyInfo(f"{obj}.e[{e_index}]", edgeToFace=True) or []
    if not info:
        return []
    line = info[0].strip()
    if ":" not in line:
        return []
    right = line.split(":", 1)[1].strip()
    faces = []
    for t in right.split():
        try:
            faces.append(int(t))
        except Exception:
            pass
    return faces

def _get_selection_info():
    """
    選択状態を解析し、オブジェクトごとに処理対象とする頂点・フェース・エッジの情報を整理して返す。
    """
    sel = cmds.ls(sl=True, long=True) or []
    if not sel:
        return {}

    objects = _get_mesh_transforms_from_selection()
    result = {}

    for obj in objects:
        # このオブジェクトに属する選択中のコンポーネントを取得
        obj_sel = cmds.ls([f"{obj}.vtx[*]", f"{obj}.e[*]", f"{obj}.f[*]"], sl=True, fl=True, long=True) or []
        
        vtx_count = cmds.polyEvaluate(obj, vertex=True) or 0
        edge_count = cmds.polyEvaluate(obj, edge=True) or 0
        
        if obj_sel:
            # --- コンポーネント選択がある場合、その範囲に限定する ---
            
            # 1. 移動対象の頂点 (選択コンポーネントから頂点リストへ変換)
            vtx_comps = cmds.polyListComponentConversion(obj_sel, toVertex=True)
            vtx_flat = cmds.ls(vtx_comps, fl=True) or []
            target_vertices = set()
            for v in vtx_flat:
                match = re.search(r"vtx\[(\d+)\]", v)
                if match:
                    target_vertices.add(int(match.group(1)))
                    
            # 2. 分割対象のフェース (選択コンポーネントからフェースリストへ変換)
            face_comps = cmds.polyListComponentConversion(obj_sel, toFace=True)
            target_faces = cmds.ls(face_comps, fl=True) or []
            
            # 3. フリップ対象のエッジ (選択コンポーネントからエッジリストへ変換)
            edge_comps = cmds.polyListComponentConversion(obj_sel, toEdge=True)
            edge_flat = cmds.ls(edge_comps, fl=True) or []
            target_edges = []
            for e in edge_flat:
                match = re.search(r"e\[(\d+)\]", e)
                if match:
                    target_edges.append(int(match.group(1)))
        else:
            # --- オブジェクト自体が選択されている場合、全体を対象にする ---
            target_vertices = set(range(vtx_count))
            target_faces = [f"{obj}.f[*]"]
            target_edges = list(range(edge_count))
            
        result[obj] = {
            "vertices": target_vertices,
            "faces": target_faces,
            "edges": target_edges
        }
        
    return result

def random_triangulate_with_options(
    jitter_amount=0.02,
    seed=1,
    lock_axes=(False, True, False),
    lock_boundary=True,
    do_triangulate=False,  # デフォルトを False に変更
    do_flip=False,
    flip_probability=0.8,
    flip_iterations=3,
    keep_history=True,
    use_object_space=False
):
    """
    選択されたコンポーネント（またはオブジェクト全体）に対してジッター移動、
    三角形分割(オプション)、ランダムエッジフリップを適用する。
    """
    sel_info = _get_selection_info()
    if not sel_info:
        cmds.warning("ポリゴンメッシュ、またはコンポーネント（頂点/エッジ/フェース）を選択してから実行してください。")
        return

    lockX, lockY, lockZ = lock_axes
    jitter_amount = float(jitter_amount)
    flip_probability = float(flip_probability)
    flip_iterations = max(1, int(flip_iterations))

    random.seed(int(seed))

    cmds.undoInfo(openChunk=True)
    try:
        for obj, info in sel_info.items():
            boundary = _boundary_vertex_indices(obj) if lock_boundary else set()
            target_vertices = info["vertices"]
            target_faces = info["faces"]
            target_edges = info["edges"]

            moved = 0
            # 頂点のループ処理
            for i in target_vertices:
                if i in boundary:
                    continue

                comp = f"{obj}.vtx[{i}]"

                # 現在の位置情報を取得
                if use_object_space:
                    x, y, z = cmds.xform(comp, q=True, os=True, t=True)
                else:
                    x, y, z = cmds.xform(comp, q=True, ws=True, t=True)

                # 各軸の移動量を計算
                dx = 0.0 if lockX else random.uniform(-jitter_amount, jitter_amount)
                dy = 0.0 if lockY else random.uniform(-jitter_amount, jitter_amount)
                dz = 0.0 if lockZ else random.uniform(-jitter_amount, jitter_amount)

                # すべてロックされている場合はスキップ
                if dx == 0.0 and dy == 0.0 and dz == 0.0:
                    continue

                # 新しい位置を適用
                if use_object_space:
                    cmds.xform(comp, os=True, t=(x + dx, y + dy, z + dz))
                else:
                    cmds.xform(comp, ws=True, t=(x + dx, y + dy, z + dz))

                moved += 1

            # 三角形分割（有効時のみ実行）
            if do_triangulate and target_faces:
                cmds.polyTriangulate(target_faces, ch=keep_history)

            # エッジフリップ（有効時のみ実行）
            flipped = 0
            if do_flip and target_edges:
                for _ in range(flip_iterations):
                    for e in target_edges:
                        if random.random() > flip_probability:
                            continue
                        faces = _edge_to_faces(obj, e)
                        if len(faces) != 2:
                            continue
                        try:
                            cmds.polyFlipEdge(f"{obj}.e[{e}]", ch=keep_history)
                            flipped += 1
                        except Exception:
                            pass

            if not keep_history:
                cmds.delete(obj, ch=True)

            msg = f"{obj} | moved_vtx={moved}" + (f" | flipped={flipped}" if do_flip else "")
            print(msg)
            cmds.inViewMessage(amg=msg, pos="midCenterTop", fade=True)

    finally:
        cmds.undoInfo(closeChunk=True)

# ---------------- UI コールバック ----------------

def _ui_on_run(*_):
    jitter = cmds.floatSliderGrp("mokaRT_jitter", q=True, v=True)
    seed = cmds.intField("mokaRT_seed", q=True, v=True)

    lockX = cmds.checkBox("mokaRT_lockX", q=True, v=True)
    lockY = cmds.checkBox("mokaRT_lockY", q=True, v=True)
    lockZ = cmds.checkBox("mokaRT_lockZ", q=True, v=True)

    lockBoundary = cmds.checkBox("mokaRT_lockBoundary", q=True, v=True)
    doTri = cmds.checkBox("mokaRT_doTri", q=True, v=True)

    doFlip = cmds.checkBox("mokaRT_doFlip", q=True, v=True)
    flipProb = cmds.floatField("mokaRT_flipProb", q=True, v=True)
    flipIter = cmds.intField("mokaRT_flipIter", q=True, v=True)

    keepHist = cmds.checkBox("mokaRT_keepHist", q=True, v=True)
    useOS = cmds.checkBox("mokaRT_useOS", q=True, v=True)

    random_triangulate_with_options(
        jitter_amount=jitter,
        seed=seed,
        lock_axes=(lockX, lockY, lockZ),
        lock_boundary=lockBoundary,
        do_triangulate=doTri,
        do_flip=doFlip,
        flip_probability=flipProb,
        flip_iterations=flipIter,
        keep_history=keepHist,
        use_object_space=useOS
    )

def _ui_on_toggle_flip(*_):
    doFlip = cmds.checkBox("mokaRT_doFlip", q=True, v=True)
    cmds.floatField("mokaRT_flipProb", e=True, en=doFlip)
    cmds.intField("mokaRT_flipIter", e=True, en=doFlip)

def _ui_on_random_seed(*_):
    new_seed = random.randint(1, 9999)
    cmds.intField("mokaRT_seed", e=True, v=new_seed)

# ---------------- UI 作成 ----------------

def show_random_triangulate_ui():
    if cmds.window(WIN_NAME, exists=True):
        cmds.deleteUI(WIN_NAME)

    cmds.window(WIN_NAME, title="Random Triangulate (Axis Lock)", sizeable=False)
    cmds.columnLayout(adjustableColumn=True, rowSpacing=8, columnAlign="left")

    cmds.frameLayout(label="Vertex Randomize", collapsable=False, marginWidth=10, marginHeight=8)
    cmds.columnLayout(adjustableColumn=True, rowSpacing=6)

    # スライダー形式で感覚的に量を調整可能
    cmds.floatSliderGrp("mokaRT_jitter", label="Jitter Amount", v=0.02, minValue=0.0, maxValue=1.0, field=True, precision=3, columnWidth3=(90, 50, 140))

    # Seedのランダムボタン
    cmds.rowLayout(numberOfColumns=3, columnWidth3=(90, 130, 60), adjustableColumn=2)
    cmds.text(label="Seed (same result) ")
    cmds.intField("mokaRT_seed", v=1)
    cmds.button(label="Random", c=_ui_on_random_seed)
    cmds.setParent("..")

    cmds.text(label="Lock Axis (checked = do NOT move)")
    cmds.rowLayout(numberOfColumns=3, columnWidth3=(100, 100, 100))
    cmds.checkBox("mokaRT_lockX", label="Lock X", v=False)
    cmds.checkBox("mokaRT_lockY", label="Lock Y", v=True)
    cmds.checkBox("mokaRT_lockZ", label="Lock Z", v=False)
    cmds.setParent("..")

    cmds.checkBox("mokaRT_lockBoundary", label="Lock Boundary (keep silhouette)", v=True)
    cmds.checkBox("mokaRT_useOS", label="Use Object Space (if rotated plane)", v=False)

    cmds.setParent("..")
    cmds.setParent("..")

    cmds.frameLayout(label="Triangulate / Random Flip", collapsable=False, marginWidth=10, marginHeight=8)
    cmds.columnLayout(adjustableColumn=True, rowSpacing=6)

    # 【変更】初期値を v=False に変更し、デフォルトでは三角形分割を行わない仕様に
    cmds.checkBox("mokaRT_doTri", label="Triangulate", v=False)

    cmds.checkBox("mokaRT_doFlip", label="Random Flip Edges (optional)", v=False, cc=_ui_on_toggle_flip)
    
    cmds.rowLayout(numberOfColumns=2, columnWidth2=(140, 140), adjustableColumn=2)
    cmds.text(label="Flip Probability")
    cmds.floatField("mokaRT_flipProb", v=0.8, minValue=0.0, maxValue=1.0, en=False)
    cmds.setParent("..")
    
    cmds.rowLayout(numberOfColumns=2, columnWidth2=(140, 140), adjustableColumn=2)
    cmds.text(label="Flip Iterations")
    cmds.intField("mokaRT_flipIter", v=3, minValue=1, en=False)
    cmds.setParent("..")

    cmds.checkBox("mokaRT_keepHist", label="Keep History", v=True)

    cmds.setParent("..")
    cmds.setParent("..")

    cmds.separator(h=8, style="in")

    cmds.rowLayout(numberOfColumns=2, columnWidth2=(155, 155))
    cmds.button(label="Run on Selected", h=32, c=_ui_on_run)
    cmds.button(label="Close", h=32, c=lambda *_: cmds.deleteUI(WIN_NAME))
    cmds.setParent("..")

    cmds.showWindow(WIN_NAME)

# UIの起動
show_random_triangulate_ui()