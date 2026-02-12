from fastapi import FastAPI, File, UploadFile, HTTPException
from fastapi.responses import Response, JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from rembg import remove
from PIL import Image
import io
from typing import List
import logging
import zipfile

# Configurar logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = FastAPI(
    title="Virtual Wardrobe Background Remover",
    description="API para remover fondos de prendas de ropa",
    version="1.0.0"
)

# CORS - permite peticiones desde React Native
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Configuración
MAX_IMAGE_WIDTH = 1024  # Ancho máximo para optimizar
BACKGROUND_COLOR = (248, 249, 250, 255)  # #f8f9fa en RGBA
MAX_BATCH_SIZE = 15


def optimize_image(image: Image.Image) -> Image.Image:
    """Redimensiona la imagen si excede el ancho máximo manteniendo aspect ratio"""
    if image.width > MAX_IMAGE_WIDTH:
        ratio = MAX_IMAGE_WIDTH / image.width
        new_height = int(image.height * ratio)
        image = image.resize((MAX_IMAGE_WIDTH, new_height), Image.Resampling.LANCZOS)
        logger.info(f"Imagen redimensionada a {MAX_IMAGE_WIDTH}x{new_height}")
    return image


def process_image(image_bytes: bytes) -> bytes:
    """
    Procesa una imagen: remueve fondo y aplica color de fondo personalizado
    Retorna los bytes de la imagen procesada
    """
    try:
        # Abrir imagen original
        input_image = Image.open(io.BytesIO(image_bytes))
        
        # Convertir a RGB si es necesario
        if input_image.mode in ('RGBA', 'LA', 'P'):
            input_image = input_image.convert('RGBA')
        else:
            input_image = input_image.convert('RGB')
        
        # Optimizar tamaño
        input_image = optimize_image(input_image)
        
        # Remover fondo con rembg
        logger.info("Removiendo fondo...")
        output_image = remove(input_image)
        
        # Crear imagen con fondo personalizado
        background = Image.new('RGBA', output_image.size, BACKGROUND_COLOR)
        background.paste(output_image, (0, 0), output_image)
        
        # Convertir a RGB para JPEG (menor tamaño que PNG)
        final_image = background.convert('RGB')
        
        # Convertir a bytes
        buffered = io.BytesIO()
        final_image.save(buffered, format="JPEG", quality=90, optimize=True)
        img_bytes = buffered.getvalue()
        
        logger.info("Imagen procesada exitosamente")
        return img_bytes
        
    except Exception as e:
        logger.error(f"Error procesando imagen: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Error procesando imagen: {str(e)}")


@app.get("/")
async def root():
    """Endpoint de bienvenida"""
    return {
        "message": "Virtual Wardrobe API - Background Removal Service",
        "endpoints": {
            "single": "/remove-background",
            "batch": "/remove-background-batch"
        },
        "status": "online"
    }


@app.get("/health")
async def health_check():
    """Health check endpoint"""
    return {"status": "healthy"}


@app.post("/remove-background")
async def remove_background_single(file: UploadFile = File(...)):
    """
    Remueve el fondo de una sola imagen
    
    Args:
        file: Imagen en formato multipart/form-data
        
    Returns:
        Imagen procesada en formato JPEG (binary)
    """
    try:
        # Validar tipo de archivo
        if not file.content_type.startswith('image/'):
            raise HTTPException(status_code=400, detail="El archivo debe ser una imagen")
        
        # Leer imagen
        logger.info(f"Procesando imagen: {file.filename}")
        image_bytes = await file.read()
        
        # Procesar
        processed_bytes = process_image(image_bytes)
        
        # Retornar imagen como binary
        return Response(
            content=processed_bytes,
            media_type="image/jpeg",
            headers={
                "Content-Disposition": f'attachment; filename="processed_{file.filename}"'
            }
        )
        
    except HTTPException as he:
        raise he
    except Exception as e:
        logger.error(f"Error en endpoint single: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/remove-background-batch")
async def remove_background_batch(files: List[UploadFile] = File(...)):
    """
    Remueve el fondo de múltiples imágenes (máximo 15)
    Procesamiento SECUENCIAL para estabilidad en Hugging Face
    
    Args:
        files: Lista de imágenes en formato multipart/form-data
        
    Returns:
        Archivo ZIP con todas las imágenes procesadas
    """
    try:
        # Validar cantidad
        if len(files) > MAX_BATCH_SIZE:
            raise HTTPException(
                status_code=400, 
                detail=f"Máximo {MAX_BATCH_SIZE} imágenes por lote. Recibidas: {len(files)}"
            )
        
        if len(files) == 0:
            raise HTTPException(status_code=400, detail="No se recibieron imágenes")
        
        logger.info(f"Procesando lote de {len(files)} imágenes")
        
        # Crear ZIP en memoria
        zip_buffer = io.BytesIO()
        
        with zipfile.ZipFile(zip_buffer, 'w', zipfile.ZIP_DEFLATED) as zip_file:
            # Procesar secuencialmente para evitar sobrecarga de memoria
            for idx, file in enumerate(files):
                try:
                    # Validar tipo
                    if not file.content_type.startswith('image/'):
                        logger.warning(f"Archivo {file.filename} no es una imagen, saltando...")
                        continue
                    
                    logger.info(f"Procesando imagen {idx + 1}/{len(files)}: {file.filename}")
                    
                    # Leer y procesar
                    image_bytes = await file.read()
                    processed_bytes = process_image(image_bytes)
                    
                    # Agregar al ZIP
                    # Usar nombre original pero asegurar que sea único
                    base_name = file.filename.rsplit('.', 1)[0] if '.' in file.filename else file.filename
                    zip_filename = f"{idx:03d}_{base_name}.jpg"
                    zip_file.writestr(zip_filename, processed_bytes)
                    
                except Exception as e:
                    logger.error(f"Error procesando {file.filename}: {str(e)}")
                    # Continuar con las demás imágenes
                    continue
        
        # Preparar ZIP para enviar
        zip_buffer.seek(0)
        zip_bytes = zip_buffer.getvalue()
        
        logger.info(f"Lote procesado exitosamente. ZIP size: {len(zip_bytes)} bytes")
        
        # Retornar ZIP
        return Response(
            content=zip_bytes,
            media_type="application/zip",
            headers={
                "Content-Disposition": 'attachment; filename="processed_garments.zip"'
            }
        )
        
    except HTTPException as he:
        raise he
    except Exception as e:
        logger.error(f"Error en endpoint batch: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=7860)
