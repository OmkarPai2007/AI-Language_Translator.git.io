import os
from PIL import Image
import google.generativeai as genai
from tkinter import Tk
from tkinter.filedialog import askopenfilename

# 1. Configure the API Key
genai.configure(api_key="YOUR_API_KEY_HERE")  # Replace with your valid key

# 2. Open file picker dialog
Tk().withdraw()  # Hide the root tkinter window
image_path = askopenfilename(
    title="Select an image",
    filetypes=[("Image Files", "*.png *.jpg *.jpeg *.bmp *.gif")]
)

if not image_path:
    print("No image selected. Exiting.")
    exit()

# 3. Load the selected image
image = Image.open(image_path)

# 4. Use the Gemini multimodal model
model = genai.GenerativeModel(model_name="gemini-1.5-flash")

# 5. Send prompt and image
prompt = "Describe this image in detail"

print("Analyzing the image...")

try:
    response = model.generate_content([prompt, image])

    # 6. Print result
    print("\nModel's response:")
    print(response.text)

except Exception as e:
    print(f"\nAn unexpected error occurred: {e}")
