<?php  
if ($_SERVER["REQUEST_METHOD"] == "POST") {      
	// Collect form data      
	$name = htmlspecialchars(trim($_POST['demo-name']));      
	$email = htmlspecialchars(trim($_POST['demo-email']));      
	$priority = htmlspecialchars(trim($_POST['demo-priority']));      
	$message = htmlspecialchars(trim($_POST['demo-message']));            
	// To send a copy to the user if checked      
	$sendCopy = isset($_POST['demo-copy']) ? true : false;        
	// Recipient email      
	$to = 'jack@jackcapstaff.com';      
	$subject = 'New Contact Form Submission';        
	// Construct email body      
	$body = "Name: $name\n";      
	$body .= "Email: $email\n";      
	$body .= "Priority: $priority\n";      
	$body .= "Message:\n$message\n";        
	// Set headers      
	$headers = "From: $email\r\n";      
	if ($sendCopy) {          
		$headers .= "Cc: $email\r\n";      
	}        
	// Send email      
	if (mail($to, $subject, $body, $headers)) {          
		echo "Message sent successfully!";      
} else {          
		echo "Message delivery failed.";      
	}  
}  
?>